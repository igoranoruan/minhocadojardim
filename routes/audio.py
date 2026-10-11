"""Rotas da EXTRAÇÃO DE ÁUDIO -- Parte A (individual): autorização do CÉREBRO, 11/10/2026.

Diferente de routes/generations.py, estas rotas são SÍNCRONAS (sem streaming SSE) -- decisão
tomada NESTA implementação (documentada no relatório final como limitação conhecida/decisão
autônoma): o pedido do CÉREBRO não exige barra de progresso em tempo real para áudio, só o
contador "X de 15 hoje" antes de começar. Manter a rota síncrona permite devolver o status HTTP
REAL (403 plano não pago / 429 limite diário / 409 operação em andamento) ANTES de qualquer
processamento -- exatamente os testes obrigatórios pedidos ("Free é bloqueado (403)", "Limite
atingido -> 429") --, em vez de escondê-los dentro de um evento SSE (que sempre responde 200).
Nenhuma rota de vídeo (routes/generations.py) é tocada ou alterada por este arquivo.

GET  /api/audio/allowance           saldo diário ("X de 15 hoje")
POST /api/audio/extract             extração avulsa (Parte A)
GET  /api/audio/{id}/download       download do MP3 já pronto
"""
import logging
import re

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database.models import User
from database.session import get_session
from routes.deps import get_current_user
from services import result_storage
from services.audio_flow import AudioPersistenceError, extract_audio_from_url
from services.audio_usage import (
    AudioDownloadNotFoundError,
    AudioUsageError,
    get_audio_allowance,
    get_downloadable_audio_extraction,
)
from services.batch_filenames import InvalidFilenameError

logger = logging.getLogger("minhoca")

router = APIRouter(prefix="/api/audio", tags=["audio"])

NO_STORE = {"Cache-Control": "no-store"}
_CAMEL_CASE_RE = re.compile(r"(?<!^)(?=[A-Z])")

# Nome padrão de download quando o usuário não escolheu um filename (pedido EXATO do CÉREBRO) --
# diferente do vídeo (klango-{8 chars do storage_key}.mp4), nunca derivado do storage_key.
AUDIO_DEFAULT_FILENAME = "audio_klango.mp3"
_AUDIO_STORAGE_EXTENSION = "mp3"


def _code_for(exc: BaseException) -> str:
    """'AudioQuotaExceededError' -> 'audio_quota_exceeded' -- mesmo estilo de código já usado em
    routes/generations.py e routes/pinterest.py (duplicado aqui de propósito, mesmo padrão do
    projeto: cada arquivo de rotas tem seu próprio `_code_for`, nunca um util compartilhado)."""
    name = exc.__class__.__name__
    if name.endswith("Error"):
        name = name[: -len("Error")]
    return _CAMEL_CASE_RE.sub("_", name).lower()


def _error_response(status_code: int, exc: BaseException, *, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"detail": detail, "code": _code_for(exc)}, headers=NO_STORE)


async def audio_usage_error_handler(request, exc: AudioUsageError) -> JSONResponse:
    """Handler ÚNICO para toda subclasse de AudioUsageError -- cada uma já carrega seu
    status_code/user_message (ver services/audio_usage.py), mesma filosofia de
    routes/pinterest.py::pinterest_board_error_handler."""
    return _error_response(exc.status_code, exc, detail=exc.detail)


async def audio_download_not_found_handler(request, exc: AudioDownloadNotFoundError) -> JSONResponse:
    return _error_response(404, exc, detail="Resultado não encontrado.")


async def audio_persistence_error_handler(request, exc: AudioPersistenceError) -> JSONResponse:
    logger.error("[AUDIO] falha de persistência (extração %s, fase %s)", exc.extraction_id, exc.phase)
    return _error_response(500, exc, detail="Não foi possível concluir o registro desta extração.")


async def audio_invalid_filename_handler(request, exc: InvalidFilenameError) -> JSONResponse:
    """InvalidFilenameError (services.batch_filenames) não tinha handler HTTP global nenhum (em
    routes/generations.py só é traduzida DENTRO do stream SSE, nunca como exceção HTTP direta) --
    como esta rota é síncrona, precisa do próprio handler. Registrar aqui não conflita com o fluxo
    de vídeo: aquele nunca deixa esta exceção escapar até o FastAPI."""
    return _error_response(400, exc, detail="Nome de arquivo inválido.")


class AllowanceOut(BaseModel):
    used: int
    limit: int
    remaining: int


class ExtractAudioBody(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    filename: str | None = Field(default=None, max_length=200)


class ExtractAudioOut(BaseModel):
    extraction_id: int
    status: str
    platform: str
    size_bytes: int
    duration_seconds: float
    output_sha256: str
    used: int
    limit: int
    remaining: int


@router.get("/allowance", response_model=AllowanceOut)
def allowance(user: User = Depends(get_current_user), db: Session = Depends(get_session)) -> AllowanceOut:
    """Saldo diário ("X de 15 hoje") -- levanta AudioNotAllowedError (403) se o plano efetivo não
    for pago. O frontend só chama esta rota quando o usuário escolhe "Só áudio" com sessão (Free/
    deslogado mostra o cadeado localmente, sem bater na API -- ver static/index.html)."""
    result = get_audio_allowance(db, user.id)
    return AllowanceOut(used=result.used, limit=result.limit, remaining=result.remaining)


@router.post("/extract", response_model=ExtractAudioOut)
def extract_audio(
    body: ExtractAudioBody,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> ExtractAudioOut:
    """Extração avulsa (Parte A). Rota fina: toda a regra de negócio mora em
    services/audio_flow.py (reserva + download + processamento + storage) e
    services/audio_usage.py (plano/limite/concorrência) -- nada disso é duplicado aqui."""
    outcome = extract_audio_from_url(db, user=user, url=body.url, filename=body.filename)
    saldo = get_audio_allowance(db, user.id)
    return ExtractAudioOut(
        extraction_id=outcome.extraction_id,
        status=outcome.status,
        platform=outcome.platform,
        size_bytes=outcome.size_bytes,
        duration_seconds=outcome.duration_seconds,
        output_sha256=outcome.output_sha256,
        used=saldo.used,
        limit=saldo.limit,
        remaining=saldo.remaining,
    )


@router.get("/{extraction_id}/download")
def download_audio(
    extraction_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> FileResponse:
    """`storage_key` nunca vem da URL/query string -- lido internamente da extração, já com
    ownership/status/expiração verificados por
    services.audio_usage.get_downloadable_audio_extraction (única fonte dessa checagem)."""
    extraction = get_downloadable_audio_extraction(db, extraction_id, user.id)
    if not result_storage.exists(extraction.output_storage_key, _AUDIO_STORAGE_EXTENSION):
        raise AudioDownloadNotFoundError()
    caminho = result_storage.resolve_path(extraction.output_storage_key, _AUDIO_STORAGE_EXTENSION)
    nome = extraction.display_filename or AUDIO_DEFAULT_FILENAME
    return FileResponse(caminho, media_type="audio/mpeg", filename=nome, headers=NO_STORE)
