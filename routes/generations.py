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
  decisão consciente, tomada com o dono do produto.

POST /api/generations e GET /api/generations/{id}/download usam get_generation_user (Free
anônimo -- aprovação do CÉREBRO): sessão autenticada OU identidade anônima por cookie, nunca 401
para um visitante sem login. As rotas de LOTE (download-batch/batches/{id}/download) continuam em
get_current_user -- lote é sempre de plano pago (services/plans.py: Free não tem lote), então
login continua obrigatório ali, sem mudança nenhuma.

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
from routes.deps import get_current_user, get_generation_user, get_optional_authenticated_user
from services import result_storage
from services.batch_filenames import InvalidFilenameError, sanitize_batch_filenames
from services.batch_fingerprint import compute_batch_fingerprint
from services.entitlements import EntitlementInconsistencyError
from services.generation_flow import GenerationPersistenceError, generate_from_url, run_batch
from services.usage import (
    BatchNotAllowedError,
    BatchNotFoundError,
    BatchQuotaExceededError,
    BatchRequestConflictError,
    GenerationDownloadNotFoundError,
    InvalidBatchSizeError,
    QuotaExceededError,
    UsageError,
    get_downloadable_batch_generations,
    get_downloadable_generation,
    reserve_batch,
)

logger = logging.getLogger("minhoca")

router = APIRouter(prefix="/api", tags=["generations"])

NO_STORE = {"Cache-Control": "no-store"}
_CAMEL_CASE_RE = re.compile(r"(?<!^)(?=[A-Z])")


class CreateGenerationBody(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    # Opcional (02/10/2026 -- mesmo campo que o item de lote já tinha desde a Etapa 9.3):
    # sanitizado por services.generation_flow.generate_from_url (services.batch_filenames) e
    # persistido em Generation.display_filename; usado pelo download individual, que já lia esse
    # campo há tempo (routes/generations.py, GET /generations/{id}/download).
    filename: str | None = Field(default=None, max_length=200)


class BatchItemBody(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    # Opcional (Etapa 9.3) -- sanitizado (services.batch_filenames) e persistido em
    # Generation.display_filename; usado no download individual e no ZIP (ver create_batch).
    filename: str | None = Field(default=None, max_length=200)


class CreateBatchBody(BaseModel):
    request_id: str = Field(min_length=1, max_length=64)
    # Só estrutura (lista, pelo menos 1 item) -- o teto de QUANTIDADE por plano (max_batch_size)
    # é regra de negócio e continua exclusivamente em reserve_batch/services.plans; não duplicado
    # aqui (correção da Etapa 9.3: um teto arbitrário no Pydantic já existiu e foi removido).
    items: list[BatchItemBody] = Field(min_length=1)


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


async def batch_not_found_handler(request, exc: BatchNotFoundError) -> JSONResponse:
    """404 genérico e único para o ZIP de lote (Etapa 9.3) -- mesma filosofia do 404 genérico da
    geração avulsa: lote inexistente, de outro usuário, ou sem nenhum item concluído disponível
    para download recebem exatamente a mesma resposta."""
    return _error_response(404, exc, detail="Lote não encontrado.")


def _error_payload_for(exc: BaseException) -> dict:
    """Mesma tradução de erro que os handlers acima já faziam via HTTP status — aqui devolvida
    como o corpo do evento `error` (SSE), já que o status HTTP não pode mais mudar depois que o
    stream começou. `code`/`detail` continuam idênticos ao que cada tipo de erro já produzia."""
    if isinstance(exc, (DownloadError, ProcessorError)):
        detail = exc.user_message
    elif isinstance(exc, InvalidFilenameError):
        detail = "Nome de arquivo inválido."
    elif isinstance(exc, QuotaExceededError):
        detail = "Você atingiu o limite de gerações do seu plano."
    elif isinstance(exc, BatchQuotaExceededError):
        detail = "Você atingiu o limite de operações de lote do seu plano nesta semana."
    elif isinstance(exc, BatchNotAllowedError):
        detail = "Seu plano não permite processamento em lote."
    elif isinstance(exc, InvalidBatchSizeError):
        detail = "Quantidade de itens inválida para este lote."
    elif isinstance(exc, BatchRequestConflictError):
        detail = "Esta requisição de lote já foi usada com um tamanho diferente."
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
    engine_bind, url: str, filename: str | None, user_id: int,
    event_queue: "queue.Queue[tuple[str, dict] | None]",
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

        def on_progress(stage: str, percent: float | None, platform: str) -> None:
            # YouTube passa pelo proxy residencial (ver YtDlpDownloader._apply_youtube_proxy) --
            # bem mais lento que as outras plataformas (download todo observado em produção em
            # ~90-130s, contra poucos segundos nas demais). Sem um aviso específico, o usuário via
            # só "Baixando seu vídeo..." parado por mais de um minuto e achava que tinha travado
            # (relato real do Igor em 02/10/2026) -- esta mensagem só existe para gerenciar essa
            # expectativa; não muda a duração real do download.
            if stage == "download" and platform == "youtube":
                mensagem = "Baixando do YouTube... pode levar até 2 minutos, aguarde."
            elif stage == "download":
                mensagem = "Baixando seu vídeo..."
            else:
                mensagem = "Processando e limpando seu vídeo..."
            tipo_evento = "progress" if stage == "processing" else "status"
            event_queue.put((tipo_evento, {"stage": stage, "percent": percent, "message": mensagem}))

        outcome = generate_from_url(session, user=user, url=url, filename=filename, on_progress=on_progress)
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


def _stream_generation(engine_bind, url: str, filename: str | None, user_id: int) -> Iterator[str]:
    event_queue: "queue.Queue[tuple[str, dict] | None]" = queue.Queue()
    thread = threading.Thread(
        target=_run_generation_in_background,
        args=(engine_bind, url, filename, user_id, event_queue),
        daemon=True,
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
    user: User = Depends(get_generation_user),
    db: Session = Depends(get_session),
) -> StreamingResponse:
    """Rota fina: só resolve o usuário (get_generation_user -- sessão autenticada OU identidade
    anônima, Free sem login) ANTES de qualquer stream, e devolve o stream. `db.get_bind()` (a
    engine, não a Session em si) é o que a thread de trabalho usa para montar sua PRÓPRIA Session
    -- ver _run_generation_in_background. Toda a orquestração real mora em
    _run_generation_in_background/_stream_generation, acima."""
    return StreamingResponse(
        _stream_generation(db.get_bind(), body.url, body.filename, user.id),
        media_type="text/event-stream",
        headers=NO_STORE,
    )


def _run_batch_in_background(
    engine_bind, items: list[dict], request_id: str, user_id: int,
    event_queue: "queue.Queue[tuple[str, dict] | None]",
) -> None:
    """Mesmo padrão de _run_generation_in_background (Session própria da thread, a partir da
    MESMA engine que a rota recebeu via Depends(get_session) -- ver o comentário daquela função,
    que vale idêntico aqui). `items` é uma lista de dicts {"url": ..., "filename": ...} NA ORDEM
    em que o cliente enviou -- essa ordem é o único vínculo entre cada item e a posição (1-based)
    que reserve_batch atribui às Generations reservadas; a URL em si não é persistida em lugar
    nenhum (só o filename passou a ser, via display_filenames -- ver services/batch_filenames.py
    e Generation.display_filename, migration 0004)."""
    session = sessionmaker(bind=engine_bind, expire_on_commit=False)()
    try:
        # 1) validação ESTRUTURAL do filename (não toca banco/cota) -- sempre antes de qualquer
        # reserva, para nunca gastar cota por causa de um filename inválido. Já sanitizado (com
        # .mp4 garantido) -- é o valor gravado em Generation.display_filename daqui pra frente.
        sanitizados = sanitize_batch_filenames([item["filename"] for item in items])

        # Fingerprint do CONTEÚDO (Etapa 9.3, correção de idempotência): urls (como enviadas) +
        # filenames JÁ sanitizados, na ordem submetida -- mesma função usada aqui e dentro de
        # reserve_batch/_batch_replay para comparar contra uma repetição do mesmo request_id.
        fingerprint = compute_batch_fingerprint(
            [(item["url"], nome) for item, nome in zip(items, sanitizados)]
        )

        reservation = reserve_batch(
            session, user_id=user_id, request_id=request_id, size=len(items),
            content_fingerprint=fingerprint, display_filenames=sanitizados,
        )
        event_queue.put((
            "batch_reserved",
            {"batch_id": reservation.batch_id, "item_count": reservation.item_count, "created": reservation.created},
        ))

        pares = list(zip(reservation.generation_ids, (item["url"] for item in items), strict=True))

        def on_item_progress(
            position: int, generation_id: int, stage: str, percent: float | None, platform: str,
        ) -> None:
            # Mesmo aviso específico do YouTube da geração avulsa, acima -- ver aquele comentário.
            if stage == "download" and platform == "youtube":
                mensagem = "Baixando do YouTube... pode levar até 2 minutos, aguarde."
            elif stage == "download":
                mensagem = "Baixando..."
            else:
                mensagem = "Processando e limpando..."
            tipo_evento = "item_progress" if stage == "processing" else "item_status"
            event_queue.put((tipo_evento, {
                "position": position, "generation_id": generation_id,
                "stage": stage, "percent": percent, "message": mensagem,
            }))

        resultados = run_batch(session, items=pares, on_item_progress=on_item_progress)

        concluidos = falhados = 0
        for resultado in resultados:
            nome = sanitizados[resultado.position - 1]
            if resultado.ok:
                concluidos += 1
                payload = {
                    "position": resultado.position,
                    "generation_id": resultado.generation_id,
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
                    "generation_id": resultado.generation_id,
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
        event_queue.put(("error", _error_payload_for(exc)))
    finally:
        session.close()
        event_queue.put(None)


def _stream_batch(engine_bind, items: list[dict], request_id: str, user_id: int) -> Iterator[str]:
    event_queue: "queue.Queue[tuple[str, dict] | None]" = queue.Queue()
    thread = threading.Thread(
        target=_run_batch_in_background,
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


@router.post("/download-batch")
def create_batch(
    body: CreateBatchBody,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> StreamingResponse:
    """Rota fina, mesmo desenho de POST /api/generations: só resolve o usuário autenticado (401
    normal, antes de qualquer stream) e devolve o stream SSE. TODA a regra de negócio (validação
    de filename, reserva de quota -- vídeos E operações de lote, Etapa 9.2 -- e a orquestração de
    cada item, reaproveitando GenerationFlow) mora em _run_batch_in_background/services.usage
    .reserve_batch/services.generation_flow.run_batch; nada disso é duplicado aqui.

    Eventos SSE (além de `error`, terminal, mesmo formato {"detail", "code"} de sempre):
    - `batch_reserved`  -- a reserva foi aceita (quota de vídeos + de operações, atômicas, Etapa 9.2)
    - `item_status`/`item_progress` -- progresso de UM item (mesmo `stage`/`percent` da geração avulsa)
    - `item_complete`   -- UM item terminou (completed ou failed) -- nunca cancela os demais
    - `batch_progress`  -- agregado (completed/failed/total) depois de CADA item_complete
    - `complete`        -- terminal, com o resumo final do lote inteiro
    """
    items = [{"url": item.url, "filename": item.filename} for item in body.items]
    return StreamingResponse(
        _stream_batch(db.get_bind(), items, body.request_id, user.id),
        media_type="text/event-stream",
        headers=NO_STORE,
    )


def _unique_zip_name(nome: str, usados: set[str]) -> str:
    """Devolve `nome` se ainda não foi usado nesta chamada de ZIP; senão gera um nome único
    inserindo um contador ANTES da extensão (`video.mp4` -> `video-2.mp4` -> `video-3.mp4`, ...),
    preservando a extensão. `usados` é mutado (registra o nome devolvido) -- chamar uma vez por
    entrada, na ordem em que são escritas no ZIP."""
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


@router.get("/batches/{batch_id}/download")
def download_batch(
    batch_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> FileResponse:
    """ZIP com os resultados dos itens CONCLUÍDOS do lote (Etapa 9.3). Itens com erro são
    simplesmente OMITIDOS (nunca incluídos como entrada vazia/corrompida). 404 genérico se o lote
    não existe, não é deste usuário, ou não tem nenhum item concluído e ainda disponível (ex.:
    todos falharam, ou todos expiraram).

    Correção arquitetural (pós-Etapa 9.3): a rota NÃO decide mais sozinha quais itens são
    elegíveis -- ownership do lote, status, storage_key, expiração e existência física do arquivo
    são inteiramente responsabilidade de services.usage.get_downloadable_batch_generations (a
    MESMA filosofia que get_downloadable_generation já aplica para a geração avulsa). A rota só
    recebe a lista já elegível (ou a BatchNotFoundError, 404 genérico) e monta o ZIP.

    Nome de cada entrada (Etapa 9.3, correção de filename): `generation.display_filename` quando
    presente (sanitizado na criação do lote, já com `.mp4` garantido -- ver
    services/batch_filenames.py), senão o padrão fixo já existente (`minhoca-{generation_id}.mp4`).
    Nomes duplicados são PERMITIDOS na criação do lote (services.batch_filenames não deduplica --
    contrato existente, preservado por esta correção); é aqui, na montagem do ZIP, que a
    deduplicação acontece de fato: `_unique_zip_name` (abaixo) garante que duas entradas com o
    MESMO nome nunca se sobrescrevam, gerando um nome único (`video.mp4` -> `video-2.mp4`)."""
    disponiveis = get_downloadable_batch_generations(db, batch_id, user.id)

    tmp_dir = Path(tempfile.mkdtemp(prefix="minhoca-batch-zip-"))
    zip_path = tmp_dir / f"minhoca-lote-{batch_id}.zip"
    nomes_usados: set[str] = set()
    try:
        with zipfile.ZipFile(zip_path, mode="w") as zip_file:
            for generation in disponiveis:
                origem = result_storage.resolve_path(generation.output_storage_key)
                # Fonte sempre um arquivo já validado por result_storage (mesma validação de
                # chave/caminho de _KEY_RE, nunca um caminho vindo do usuário); o NOME da entrada é
                # que pode vir do usuário (display_filename, já sanitizado) -- nunca usado como
                # caminho, só como arcname de um arquivo plano (sem "/", garantido na sanitização).
                nome_base = generation.display_filename or f"minhoca-{generation.id}.mp4"
                zip_file.write(origem, arcname=_unique_zip_name(nome_base, nomes_usados))
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename=f"minhoca-lote-{batch_id}.zip",
        headers=NO_STORE,
        background=BackgroundTask(shutil.rmtree, tmp_dir, ignore_errors=True),
    )


@router.get("/generations/{generation_id}/download")
def download_generation(
    generation_id: int,
    user: User = Depends(get_generation_user),
    authenticated: User | None = Depends(get_optional_authenticated_user),
    db: Session = Depends(get_session),
) -> FileResponse:
    """Etapa 8B.3. `storage_key` NUNCA vem da URL/query string — é lido internamente da geração,
    já com ownership/status/expiração verificados por services.usage.get_downloadable_generation
    (única fonte dessa checagem, não duplicada aqui) -- o ownership por user_id vale IGUAL para
    identidade anônima (Free sem login, get_generation_user): o dono é o user_id que fez a
    reserva, autenticado ou não, e um dispositivo diferente nunca enxerga a geração de outro
    (mesma checagem de sempre, nenhuma duplicação de lógica). Serve tanto geração avulsa quanto
    item de lote -- nenhuma rota separada para lote (Etapa 9.3).

    `authenticated` (correção da quota Free, aprovação do CÉREBRO): a identidade EFETIVA de
    get_generation_user (`user`) é a do dispositivo quando a sessão não tem plano pago -- mas uma
    geração antiga pertencente à própria CONTA autenticada precisa continuar baixável por ela
    mesmo assim. `get_optional_authenticated_user` nunca cria nem resolve identidade anônima, só
    diz se HÁ uma sessão de verdade -- o cookie Free em si continua incapaz de baixar uma geração
    de outra conta (ver services.usage.get_downloadable_generation, parâmetro
    `authenticated_user_id`).

    Nome do arquivo (Etapa 9.3, correção de filename): `generation.display_filename` quando
    presente (sanitizado na criação do lote, já com `.mp4` garantido), senão o padrão fixo de
    sempre (`minhoca-{generation_id}.mp4`) -- geração avulsa nunca tem display_filename, então o
    comportamento dela é IDÊNTICO ao de antes desta correção."""
    generation = get_downloadable_generation(
        db, generation_id, user.id, authenticated_user_id=authenticated.id if authenticated else None,
    )
    if not result_storage.exists(generation.output_storage_key):
        raise GenerationDownloadNotFoundError()
    caminho = result_storage.resolve_path(generation.output_storage_key)
    nome_arquivo = generation.display_filename or f"minhoca-{generation_id}.mp4"
    return FileResponse(
        caminho,
        media_type="video/mp4",
        filename=nome_arquivo,
        headers=NO_STORE,
    )
