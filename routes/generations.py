"""Rota da geração individual: POST /api/generations.

A rota fica FINA de propósito: só resolve o usuário autenticado e devolve um stream (SSE) que
outra função monta. Toda a regra de negócio mora em services/generation_flow.py, que não sabe
nada de HTTP nem de streaming.

POST /api/generations agora devolve `text/event-stream` (barra de progresso real, ver
processor/ffmpeg.py e services/generation_flow.py), não mais um único JSON. Eventos:
- `event: status`   -- fase sem percentual real (hoje só a de download; indeterminado de propósito)
- `event: progress` -- fase de processamento, com percentual REAL (ou null se a duração do vídeo
  de entrada não for conhecida) -- NUNCA um percentual inventado
- `event: complete` -- MESMOS campos que a resposta de sucesso já devolvia antes desta etapa
- `event: error`    -- evento TERMINAL único para qualquer erro depois que o stream começou
  (download inválido, cota esgotada, falha de processamento, etc.) -- ver `_error_payload_for`.
  Antes desta etapa esses erros viravam status HTTP diferentes (422/429/503/500); como a resposta
  já commitou em 200 assim que o stream abre, não é mais possível trocar o status no meio —
  decisão consciente, tomada com o dono do produto. 401 (sem sessão) continua HTTP 401 normal,
  porque get_current_user roda ANTES de qualquer stream começar.

Erros: {"detail": "...", "code": "..."} dentro do evento `error` -- mesmo formato de sempre, só
que agora no corpo do evento em vez do corpo da resposta HTTP. Nenhuma classe de erro das Etapas
3-8B foi alterada para caber aqui — a tradução continua só nesta camada.

GET /api/generations/{id}/download NÃO muda nesta etapa -- continua um JSONResponse/FileResponse
comum, sem streaming de progresso (baixar o resultado já pronto não tem "progresso" a mostrar).
"""
import json
import logging
import queue
import re
import threading
from collections.abc import Iterator

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, sessionmaker

from database.models import User
from database.session import get_session
from download.errors import DownloadError
from processor.errors import ProcessorError
from routes.deps import get_current_user
from services import result_storage
from services.entitlements import EntitlementInconsistencyError
from services.generation_flow import GenerationPersistenceError, generate_from_url
from services.usage import (
    GenerationDownloadNotFoundError,
    QuotaExceededError,
    UsageError,
    get_downloadable_generation,
)

logger = logging.getLogger("minhoca")

router = APIRouter(prefix="/api", tags=["generations"])

NO_STORE = {"Cache-Control": "no-store"}
_CAMEL_CASE_RE = re.compile(r"(?<!^)(?=[A-Z])")


class CreateGenerationBody(BaseModel):
    url: str = Field(min_length=1, max_length=2048)


def _code_for(exc: BaseException) -> str:
    """'QuotaExceededError' -> 'quota_exceeded' (mesmo estilo de código já usado em auth/usage)."""
    name = exc.__class__.__name__
    if name.endswith("Error"):
        name = name[: -len("Error")]
    return _CAMEL_CASE_RE.sub("_", name).lower()


def _error_response(status_code: int, exc: BaseException, *, detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"detail": detail, "code": _code_for(exc)},
        headers=NO_STORE,
    )


async def download_error_handler(request, exc: DownloadError) -> JSONResponse:
    """URL inválida, plataforma não suportada, SSRF bloqueado, download falhou etc. — em todos os
    casos a mensagem já vem pronta e segura em exc.user_message (nunca a URL/detalhe técnico)."""
    return _error_response(422, exc, detail=exc.user_message)


async def processor_error_handler(request, exc: ProcessorError) -> JSONResponse:
    return _error_response(422, exc, detail=exc.user_message)


async def quota_exceeded_handler(request, exc: QuotaExceededError) -> JSONResponse:
    return _error_response(429, exc, detail="Você atingiu o limite de gerações do seu plano.")


async def usage_error_handler(request, exc: UsageError) -> JSONResponse:
    """Rede de segurança para outras UsageError (ex.: futuras regras) que não sejam cota
    esgotada — não deveria ocorrer nesta rota (sem lote), mas não deve virar 500 genérico."""
    return _error_response(400, exc, detail="Não foi possível processar esta geração.")


async def entitlement_inconsistency_handler(request, exc: EntitlementInconsistencyError) -> JSONResponse:
    """Sobreposição de entitlements é inconsistência de DADOS (Etapa 4), não erro do usuário:
    503, para sinalizar que é temporário/precisa de investigação, sem detalhar a causa."""
    logger.error("[GENERATION] inconsistência de entitlements: %s", exc)
    return _error_response(503, exc, detail="Não foi possível verificar seu acesso agora.")


async def generation_persistence_error_handler(request, exc: GenerationPersistenceError) -> JSONResponse:
    """Download/processamento terminaram, mas a gravação no banco falhou — problema nosso, não
    do vídeo/usuário. Nunca expõe a fase (complete/fail) nem a causa original ao cliente."""
    logger.error("[GENERATION] falha de persistência (geração %s, fase %s)", exc.generation_id, exc.phase)
    return _error_response(500, exc, detail="Não foi possível concluir o registro desta geração.")


async def generation_download_not_found_handler(request, exc: GenerationDownloadNotFoundError) -> JSONResponse:
    """404 genérico e único para TODO motivo de download indisponível (Etapa 8B.3): geração
    inexistente, de outro usuário, ainda reserved/failed, sem storage_key, expirada, ou o arquivo
    físico ausente — deliberadamente a MESMA resposta para todos, para nunca dar a quem pergunta
    uma forma de distinguir "não existe" de "não é seu" de "expirou"."""
    return _error_response(404, exc, detail="Resultado não encontrado.")


def _error_payload_for(exc: BaseException) -> dict:
    """Mesma tradução de erro que os handlers acima já faziam via HTTP status — aqui devolvida
    como o corpo do evento `error` (SSE), já que o status HTTP não pode mais mudar depois que o
    stream começou. `code`/`detail` continuam idênticos ao que cada tipo de erro já produzia."""
    if isinstance(exc, (DownloadError, ProcessorError)):
        detail = exc.user_message
    elif isinstance(exc, QuotaExceededError):
        detail = "Você atingiu o limite de gerações do seu plano."
    elif isinstance(exc, EntitlementInconsistencyError):
        logger.error("[GENERATION] inconsistência de entitlements: %s", exc)
        detail = "Não foi possível verificar seu acesso agora."
    elif isinstance(exc, GenerationPersistenceError):
        logger.error("[GENERATION] falha de persistência (geração %s, fase %s)", exc.generation_id, exc.phase)
        detail = "Não foi possível concluir o registro desta geração."
    elif isinstance(exc, UsageError):
        detail = "Não foi possível processar esta geração."
    else:
        # Antes desta etapa, um erro inesperado aqui viraria o 500 padrão do FastAPI (fora do
        # nosso controle). Com streaming, precisamos de um evento terminal explícito -- senão o
        # cliente fica com a barra de progresso parada para sempre, sem nenhum sinal de erro.
        logger.error("[GENERATION] erro inesperado durante o streaming: %s", exc.__class__.__name__, exc_info=True)
        detail = "Não foi possível processar o vídeo."
    return {"detail": detail, "code": _code_for(exc)}


def _sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _run_generation_in_background(
    engine_bind, url: str, user_id: int, event_queue: "queue.Queue[tuple[str, dict] | None]",
) -> None:
    """Roda numa thread própria (daemon: nunca impede o processo de encerrar), com sua PRÓPRIA
    Session -- nunca a mesma nem compartilhada com a thread que monta a resposta (Session não é
    thread-safe para acesso concorrente). `engine_bind` é o `db.get_bind()` da Session que
    Depends(get_session) já injetou na rota -- ou seja, a MESMA engine que os testes já sabem
    sobrescrever via app.dependency_overrides (nunca o singleton global de produção
    database.session.get_sessionmaker(), que ignoraria esse override e apontaria pro banco
    errado nos testes). Criar uma Session NOVA a partir dessa engine, em vez de reaproveitar a
    Session `db` em si, evita depender de QUANDO exatamente o FastAPI fecha uma Session injetada
    por Depends(...) numa StreamingResponse -- não é garantido acontecer só depois do stream
    terminar de enviar em toda versão do FastAPI (só corrigido na 0.118.0; nosso requirements.txt
    não fixa um teto de versão). `user_id` (um int simples, sempre seguro de repassar entre
    threads) é usado para buscar o User de novo, JÁ dentro desta sessão própria -- nunca
    reaproveitando o objeto ORM carregado pela sessão da requisição original.

    Preserva exatamente: reserva/quota, status da geração, limpeza dos temporários e todo o
    tratamento de exceções já existentes em generate_from_url -- nada disso muda; só o RESULTADO
    (sucesso ou exceção) passa a virar um evento na fila em vez de um retorno de função direto."""
    session = sessionmaker(bind=engine_bind, expire_on_commit=False)()
    try:
        user = session.get(User, user_id)

        def on_progress(stage: str, percent: float | None) -> None:
            mensagem = "Baixando seu vídeo..." if stage == "download" else "Processando e limpando seu vídeo..."
            tipo_evento = "progress" if stage == "processing" else "status"
            event_queue.put((tipo_evento, {"stage": stage, "percent": percent, "message": mensagem}))

        outcome = generate_from_url(session, user=user, url=url, on_progress=on_progress)
        event_queue.put((
            "complete",
            {
                "generation_id": outcome.generation_id,
                "status": outcome.status,
                "platform": outcome.platform,
                "size_bytes": outcome.size_bytes,
                "duration_seconds": outcome.duration_seconds,
                "output_sha256": outcome.output_sha256,
            },
        ))
    except Exception as exc:
        event_queue.put(("error", _error_payload_for(exc)))
    finally:
        session.close()
        event_queue.put(None)  # sinaliza o fim do stream para _stream_generation


def _stream_generation(engine_bind, url: str, user_id: int) -> Iterator[str]:
    event_queue: "queue.Queue[tuple[str, dict] | None]" = queue.Queue()
    thread = threading.Thread(
        target=_run_generation_in_background, args=(engine_bind, url, user_id, event_queue), daemon=True,
    )
    thread.start()
    while True:
        item = event_queue.get()
        if item is None:
            break
        event_type, data = item
        yield _sse_event(event_type, data)


@router.post("/generations")
def create_generation(
    body: CreateGenerationBody,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> StreamingResponse:
    """Rota fina: só resolve a sessão (get_current_user, ANTES de qualquer stream -- 401 continua
    HTTP 401 normal) e devolve o stream. `db.get_bind()` (a engine, não a Session em si) é o que
    a thread de trabalho usa para montar sua PRÓPRIA Session -- ver _run_generation_in_background.
    Toda a orquestração real mora em _run_generation_in_background/_stream_generation, acima."""
    return StreamingResponse(
        _stream_generation(db.get_bind(), body.url, user.id),
        media_type="text/event-stream",
        headers=NO_STORE,
    )


@router.get("/generations/{generation_id}/download")
def download_generation(
    generation_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> FileResponse:
    """Etapa 8B.3. `storage_key` NUNCA vem da URL/query string — é lido internamente da geração,
    já com ownership/status/expiração verificados por services.usage.get_downloadable_generation
    (única fonte dessa checagem, não duplicada aqui)."""
    generation = get_downloadable_generation(db, generation_id, user.id)
    if not result_storage.exists(generation.output_storage_key):
        raise GenerationDownloadNotFoundError()
    caminho = result_storage.resolve_path(generation.output_storage_key)
    return FileResponse(
        caminho,
        media_type="video/mp4",
        filename=f"minhoca-{generation_id}.mp4",
        headers=NO_STORE,
    )
