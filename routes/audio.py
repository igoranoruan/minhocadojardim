"""Rotas da EXTRAÇÃO DE ÁUDIO -- Parte A (individual) e Parte B (lote): autorização do CÉREBRO,
11/10/2026.

Parte A é SÍNCRONA (sem streaming SSE) -- decisão tomada NESTA implementação (documentada no
relatório final como limitação conhecida/decisão autônoma): o pedido do CÉREBRO não exige barra
de progresso em tempo real para a extração AVULSA, só o contador "X de 15 hoje" antes de começar.
Manter a rota síncrona permite devolver o status HTTP REAL (403 plano não pago / 429 limite
diário / 409 operação em andamento) ANTES de qualquer processamento -- exatamente os testes
obrigatórios pedidos ("Free é bloqueado (403)", "Limite atingido -> 429") --, em vez de escondê-
los dentro de um evento SSE (que sempre responde 200).

Parte B (lote) é SSE, ao contrário da Parte A -- decisão deliberada, diferente da avulsa: o
próprio pedido do CÉREBRO para o lote exige reaproveitar "a infraestrutura de lote já existente
(processamento sequencial, progresso por item, ZIP final)", que em routes/generations.py já é
SSE (POST /api/download-batch) -- manter o MESMO desenho evita reinventar o streaming de
progresso por item só para áudio. Os erros ANTES da reserva (plano não pago, lote maior que o
permitido, etc.) viram eventos `error` {"detail","code"} dentro do stream (sempre HTTP 200 depois
que o stream abre), exatamente como já acontece para o lote de vídeo -- ver
_error_payload_for_audio, abaixo.

Nenhuma rota de vídeo (routes/generations.py) é tocada ou alterada por este arquivo.

GET  /api/audio/allowance              saldo diário ("X de 15 hoje")
POST /api/audio/extract                extração avulsa (Parte A, síncrona)
GET  /api/audio/{id}/download          download do MP3 já pronto (avulsa OU item de lote)
POST /api/audio/batch                  lote de extrações de áudio (Parte B, SSE)
GET  /api/audio/batches/{id}/download  ZIP "klango_audios.zip" com os itens concluídos do lote
"""
import json
import logging
import queue
import re
import shutil
import tempfile
import threading
import zipfile
from collections.abc import Iterator
from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, sessionmaker
from starlette.background import BackgroundTask

from database.models import User
from database.session import get_session
from download.errors import DownloadError
from processor.errors import ProcessorError
from routes.deps import get_current_user
from services import result_storage
from services.audio_flow import AudioPersistenceError, extract_audio_from_url, run_audio_batch
from services.audio_usage import (
    AudioBatchNotFoundError,
    AudioBatchRequestConflictError,
    AudioConcurrentOperationError,
    AudioDownloadNotFoundError,
    AudioNotAllowedError,
    AudioQuotaExceededError,
    AudioUsageError,
    InvalidAudioBatchSizeError,
    get_audio_allowance,
    get_downloadable_audio_batch_extractions,
    get_downloadable_audio_extraction,
    reserve_audio_batch,
)
from services.batch_filenames import InvalidFilenameError, sanitize_batch_filenames
from services.batch_fingerprint import compute_batch_fingerprint

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
    como a rota de extração avulsa é síncrona, precisa do próprio handler. Registrar aqui não
    conflita com o fluxo de vídeo: aquele nunca deixa esta exceção escapar até o FastAPI. Para o
    lote (Parte B, SSE), esta mesma exceção é tratada DENTRO do stream, nunca chega a este handler
    (ver _error_payload_for_audio/_run_audio_batch_in_background, abaixo)."""
    return _error_response(400, exc, detail="Nome de arquivo inválido.")


async def audio_batch_not_found_handler(request, exc: AudioBatchNotFoundError) -> JSONResponse:
    """404 genérico para o ZIP do lote de áudio (GET /api/audio/batches/{id}/download) -- mesma
    filosofia de audio_download_not_found_handler/batch_not_found_handler (vídeo)."""
    return _error_response(404, exc, detail="Lote de áudio não encontrado.")


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


# ============================================================================================
# Parte B -- lote de extração de áudio (SSE), autorização do CÉREBRO, 11/10/2026.
# Mesmo desenho de POST /api/download-batch (routes/generations.py): rota fina, thread própria
# com sua PRÓPRIA Session (criada a partir da MESMA engine que a rota recebeu via
# Depends(get_session) -- nunca a Session da requisição, que não é thread-safe), eventos SSE
# via Queue. Nenhuma linha de routes/generations.py é tocada ou importada daqui.
# ============================================================================================
class AudioBatchItemBody(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    filename: str | None = Field(default=None, max_length=200)


class CreateAudioBatchBody(BaseModel):
    request_id: str = Field(min_length=1, max_length=64)
    # Só estrutura (lista, pelo menos 1 item) -- o teto REAL (min(plan.max_batch_size, saldo
    # diário restante)) é regra de negócio e continua exclusivamente em
    # services.audio_usage.reserve_audio_batch; não duplicado aqui (mesmo padrão de
    # routes/generations.py::CreateBatchBody).
    items: list[AudioBatchItemBody] = Field(min_length=1)


def _error_payload_for_audio(exc: BaseException) -> dict:
    """Mesma tradução de erro que os handlers HTTP da Parte A já fazem, só que aqui devolvida
    como o corpo do evento `error` (SSE) -- o status HTTP não pode mais mudar depois que o
    stream de lote já abriu (mesmo motivo de routes/generations.py::_error_payload_for, que esta
    função espelha para as exceções de áudio)."""
    if isinstance(exc, (DownloadError, ProcessorError)):
        detail = exc.user_message
    elif isinstance(exc, InvalidFilenameError):
        detail = "Nome de arquivo inválido."
    elif isinstance(exc, AudioUsageError):
        # AudioNotAllowedError/AudioQuotaExceededError/AudioConcurrentOperationError/
        # InvalidAudioBatchSizeError/AudioBatchRequestConflictError já carregam a mensagem exata
        # em `detail` (ver services/audio_usage.py) -- nunca reescrita aqui.
        detail = exc.detail
    elif isinstance(exc, AudioPersistenceError):
        logger.error("[AUDIO_BATCH] falha de persistência (extração %s, fase %s)", exc.extraction_id, exc.phase)
        detail = "Não foi possível concluir o registro desta extração."
    else:
        logger.error("[AUDIO_BATCH] erro inesperado durante o streaming: %s", exc.__class__.__name__, exc_info=True)
        detail = "Não foi possível processar o lote de áudio."
    return {"detail": detail, "code": _code_for(exc)}


def _sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _run_audio_batch_in_background(
    engine_bind, items: list[dict], request_id: str, user_id: int,
    event_queue: "queue.Queue[tuple[str, dict] | None]",
) -> None:
    """Mesmo padrão de routes/generations.py::_run_batch_in_background -- ver aquele comentário
    (Session própria da thread, `items` é uma lista de dicts {"url", "filename"} NA ORDEM
    submetida, vínculo por posição com AudioBatchReservation.extraction_ids)."""
    session = sessionmaker(bind=engine_bind, expire_on_commit=False)()
    try:
        sanitizados = sanitize_batch_filenames([item["filename"] for item in items], suffix=".mp3")
        fingerprint = compute_batch_fingerprint(
            [(item["url"], nome) for item, nome in zip(items, sanitizados)]
        )

        reservation = reserve_audio_batch(
            session, user_id=user_id, request_id=request_id, size=len(items),
            content_fingerprint=fingerprint, display_filenames=sanitizados,
        )
        event_queue.put((
            "batch_reserved",
            {"batch_id": reservation.batch_id, "item_count": reservation.item_count, "created": reservation.created},
        ))

        pares = list(zip(reservation.extraction_ids, (item["url"] for item in items), strict=True))

        def on_item_progress(
            position: int, extraction_id: int, stage: str, percent: float | None, platform: str,
        ) -> None:
            if stage == "download" and platform == "youtube":
                mensagem = "Baixando do YouTube... pode levar até 2 minutos, aguarde."
            elif stage == "download":
                mensagem = "Baixando..."
            else:
                mensagem = "Extraindo o áudio..."
            tipo_evento = "item_progress" if stage == "processing" else "item_status"
            event_queue.put((tipo_evento, {
                "position": position, "extraction_id": extraction_id,
                "stage": stage, "percent": percent, "message": mensagem,
            }))

        resultados = run_audio_batch(session, items=pares, on_item_progress=on_item_progress)

        concluidos = falhados = 0
        for resultado in resultados:
            nome = sanitizados[resultado.position - 1]
            if resultado.ok:
                concluidos += 1
                payload = {
                    "position": resultado.position,
                    "extraction_id": resultado.extraction_id,
                    "status": "completed",
                    "platform": resultado.outcome.platform,
                    "size_bytes": resultado.outcome.size_bytes,
                    "duration_seconds": resultado.outcome.duration_seconds,
                    "output_sha256": resultado.outcome.output_sha256,
                    "filename": nome,
                }
            else:
                falhados += 1
                payload = {
                    "position": resultado.position,
                    "extraction_id": resultado.extraction_id,
                    "status": "failed",
                    "error_code": resultado.error_code,
                    "filename": nome,
                }
            event_queue.put(("item_complete", payload))
            event_queue.put((
                "batch_progress",
                {"completed": concluidos, "failed": falhados, "total": len(resultados)},
            ))

        event_queue.put((
            "complete",
            {
                "batch_id": reservation.batch_id,
                "item_count": reservation.item_count,
                "completed": concluidos,
                "failed": falhados,
            },
        ))
    except Exception as exc:
        event_queue.put(("error", _error_payload_for_audio(exc)))
    finally:
        session.close()
        event_queue.put(None)


def _stream_audio_batch(engine_bind, items: list[dict], request_id: str, user_id: int) -> Iterator[str]:
    event_queue: "queue.Queue[tuple[str, dict] | None]" = queue.Queue()
    thread = threading.Thread(
        target=_run_audio_batch_in_background,
        args=(engine_bind, items, request_id, user_id, event_queue),
        daemon=True,
    )
    thread.start()
    while True:
        item = event_queue.get()
        if item is None:
            break
        event_type, data = item
        yield _sse_event(event_type, data)


@router.post("/batch")
def create_audio_batch(
    body: CreateAudioBatchBody,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> StreamingResponse:
    """Lote de extração de áudio (Parte B). Rota fina, mesmo desenho de POST /api/download-batch:
    só resolve o usuário autenticado (401 normal, antes de qualquer stream) e devolve o stream
    SSE. TODA a regra de negócio (validação de filename com suffix=".mp3", reserva -- plano pago +
    "uma operação por vez" + min(max_batch_size, saldo diário restante), Etapa B -- e a
    orquestração de cada item, reaproveitando services/audio_flow.py) mora em
    _run_audio_batch_in_background/services.audio_usage.reserve_audio_batch/
    services.audio_flow.run_audio_batch; nada disso é duplicado aqui.

    Eventos SSE (além de `error`, terminal, mesmo formato {"detail", "code"} de sempre):
    - `batch_reserved`  -- a reserva foi aceita (plano pago + saldo diário, atômicos)
    - `item_status`/`item_progress` -- progresso de UM item (mesmo `stage`/`percent` da avulsa)
    - `item_complete`   -- UM item terminou (completed ou failed) -- nunca cancela os demais
    - `batch_progress`  -- agregado (completed/failed/total) depois de CADA item_complete
    - `complete`        -- terminal, com o resumo final do lote inteiro
    """
    items = [{"url": item.url, "filename": item.filename} for item in body.items]
    return StreamingResponse(
        _stream_audio_batch(db.get_bind(), items, body.request_id, user.id),
        media_type="text/event-stream",
        headers=NO_STORE,
    )


@router.get("/batches/{batch_id}/download")
def download_audio_batch(
    batch_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> FileResponse:
    """ZIP com os resultados dos itens CONCLUÍDOS do lote de áudio -- pedido EXATO do CÉREBRO:
    nome FIXO "klango_audios.zip" (diferente do lote de vídeo, que usa um token aleatório no nome
    -- aqui não há a mesma preocupação de "o nome denuncia o volume de lotes já criados", porque
    o CÉREBRO pediu explicitamente este nome fixo). Itens com erro são simplesmente OMITIDOS.
    404 genérico (AudioBatchNotFoundError) se o lote não existe, não é deste usuário, ou não tem
    nenhum item concluído e ainda disponível.

    Ownership/status/storage_key/expiração/existência física são inteiramente responsabilidade de
    services.audio_usage.get_downloadable_audio_batch_extractions -- a rota só recebe a lista já
    elegível e monta o ZIP, mesma arquitetura de routes/generations.py::download_batch."""
    disponiveis = get_downloadable_audio_batch_extractions(db, batch_id, user.id)

    tmp_dir = Path(tempfile.mkdtemp(prefix="minhoca-audio-batch-zip-"))
    zip_path = tmp_dir / "klango_audios.zip"
    nomes_usados: set[str] = set()
    try:
        with zipfile.ZipFile(zip_path, mode="w") as zip_file:
            for extraction in disponiveis:
                origem = result_storage.resolve_path(extraction.output_storage_key, _AUDIO_STORAGE_EXTENSION)
                nome_base = extraction.display_filename or f"audio_klango-{extraction.output_storage_key[:8]}.mp3"
                zip_file.write(origem, arcname=_unique_zip_name(nome_base, nomes_usados))
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename="klango_audios.zip",
        headers=NO_STORE,
        background=BackgroundTask(shutil.rmtree, tmp_dir, ignore_errors=True),
    )


def _unique_zip_name(nome: str, usados: set[str]) -> str:
    """Mesma lógica de routes/generations.py::_unique_zip_name (duplicada de propósito -- mesma
    convenção já usada entre routes/audio.py e routes/generations.py de nunca compartilhar
    função de rota entre os dois arquivos, só services/): devolve `nome` se ainda não foi usado
    nesta chamada de ZIP; senão gera um nome único inserindo um contador antes da extensão."""
    if nome not in usados:
        usados.add(nome)
        return nome
    raiz, ponto, extensao = nome.rpartition(".")
    base, sufixo = (raiz, f".{extensao}") if ponto else (nome, "")
    contador = 2
    while True:
        candidato = f"{base}-{contador}{sufixo}"
        if candidato not in usados:
            usados.add(candidato)
            return candidato
        contador += 1
