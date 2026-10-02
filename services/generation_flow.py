"""Orquestração da geração individual: URL -> reserva -> download -> processamento -> storage ->
conclusão.

Fluxo obrigatório (autorização da Etapa 7, estendido pela Etapa 8B.2):

    validar URL -> detectar plataforma -> reservar 1 geração -> download -> processamento
    -> result_storage.save() -> complete_generation -> limpeza dos temporários
    -> devolver GenerationOutcome

Este módulo NÃO importa routes/, NÃO conhece HTTP e NÃO cria nenhum contador de uso próprio —
usa exclusivamente services.usage.reserve_generation/complete_generation/fail_generation, que já
são a única fonte de consumo (Etapa 4). Reaproveita download.service.download_video (Etapa 5),
processor.service.process_video (Etapa 6) e services.result_storage.save (Etapa 8B.2) sem alterar
nenhum dos três além do já autorizado em complete_generation (Etapa 8B.2).

Este módulo não traduz nada para HTTP: deixa propagar as exceções já existentes das camadas
reaproveitadas (DownloadError, ProcessorError, UsageError, EntitlementError) e define só
GenerationPersistenceError, para o caso novo desta etapa (falha ao GRAVAR o resultado, depois do
download/processamento/storage já terem terminado). A tradução para HTTP é feita em
routes/generations.py.

Regra de falha do storage (Etapa 8B.2): se result_storage.save() falhar, é tratado exatamente
como uma falha de download/processamento (fail_generation, cota liberada). Se o save() tiver
sucesso mas complete_generation() falhar DEPOIS, a regra de "nunca chamar fail_generation() nesse
caso" (Etapa 7) é preservada sem alteração — o arquivo já salvo vira órfão do banco, e é removido
com uma limpeza best-effort (result_storage.delete), sem mascarar o GenerationPersistenceError
original.
"""
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from config import RESULT_TTL_SECONDS
from database.models import Generation, User
from download.errors import DownloadError
from download.platform import Platform, detect_platform
from download.service import download_video
from download.url_safety import validate_url
from processor.errors import ProcessorError
from processor.service import process_video
from services import result_storage
from services.batch_filenames import sanitize_batch_filename
from services.usage import complete_generation, fail_generation, reserve_generation
from utils.time_sp import resolve_now

logger = logging.getLogger("minhoca")

# Mesmo limite de services.usage.fail_generation (MAX_ERROR_CODE_LENGTH), reaproveitado aqui só
# para cortar o nome da exceção com segurança — não é uma regra nova, é o limite que já existe.
_ERROR_CODE_MAX_LENGTH = 64


@dataclass(frozen=True)
class GenerationOutcome:
    """Resultado de uma geração concluída com sucesso. Não inclui o arquivo em si (esta etapa não
    entrega/armazena nada — ver a limpeza no final de generate_from_url)."""

    generation_id: int
    status: str  # sempre "completed" quando este objeto é devolvido
    platform: str
    size_bytes: int
    duration_seconds: float
    output_sha256: str


class GenerationPersistenceError(Exception):
    """A gravação do resultado no banco falhou DEPOIS do download/processamento já terem
    terminado (com sucesso ou com uma falha já tratada). Isto é DELIBERADAMENTE diferente de
    DownloadError/ProcessorError: o vídeo pode ter sido processado com sucesso, mas o banco não
    pôde ser atualizado — não é uma falha do vídeo, é uma falha de persistência. `phase` diz em
    qual chamada isso aconteceu ("complete" ou "fail"), só para log; nunca é exposto ao usuário.
    """

    def __init__(self, generation_id: int, phase: str, original: BaseException) -> None:
        self.generation_id = generation_id
        self.phase = phase
        self.original = original
        super().__init__(f"falha ao persistir a geração {generation_id} (fase={phase}): {original!r}")


def _error_code_for(exc: BaseException) -> str:
    """Categoria curta para error_code, a partir do TIPO da exceção — nunca a mensagem crua (que
    pode conter detalhe técnico). Cabe sempre no limite de fail_generation."""
    return exc.__class__.__name__[:_ERROR_CODE_MAX_LENGTH]


def _cleanup_quietly(*paths: Path | None) -> None:
    """Remove os arquivos temporários (download e/ou saída processada). Nunca levanta: a limpeza
    não pode mascarar o resultado real do fluxo, seja ele sucesso ou uma exceção já capturada."""
    for path in paths:
        if path is None:
            continue
        try:
            if path.exists():
                path.unlink()
        except OSError:
            logger.warning("[GENERATION] falha ao remover arquivo temporário %s", path.name)


def _mark_failed(db: Session, generation_id: int, original_exc: BaseException) -> None:
    """Marca a geração como FAILED (libera a cota). Se a própria gravação falhar, isso vira
    GenerationPersistenceError — nunca silenciada, mas também nunca confundida com a causa
    original (que fica como __cause__ da exceção encadeada, preservada para o log)."""
    try:
        fail_generation(db, generation_id, error_code=_error_code_for(original_exc))
    except Exception as persistence_exc:
        logger.error(
            "[GENERATION] falha ao gravar FAILED da geração %s (causa original: %s): %s",
            generation_id, original_exc.__class__.__name__, persistence_exc.__class__.__name__,
            exc_info=True,
        )
        raise GenerationPersistenceError(generation_id, "fail", persistence_exc) from original_exc


def generate_from_url(
    db: Session, *, user: User, url: str, filename: str | None = None,
    on_progress: Callable[[str, float | None], None] | None = None,
) -> GenerationOutcome:
    """Executa o fluxo completo de UMA geração individual para `user`.

    Levanta, sem capturar (a tradução para HTTP é da rota):
    - DownloadError/subclasses: URL inválida, plataforma não suportada, SSRF bloqueado, etc. —
      levantadas pela VALIDAÇÃO, antes de qualquer reserva (nenhuma cota é tocada); ou pelo
      DOWNLOAD em si, depois da reserva (nesse caso a geração já foi marcada FAILED antes de
      relançar).
    - QuotaExceededError / outras UsageError, EntitlementError: levantadas por
      services.usage.reserve_generation, antes de qualquer download (nenhum arquivo é criado).
    - ProcessorError/subclasses: falha no processamento, depois da reserva — a geração já foi
      marcada FAILED antes de relançar.
    - GenerationPersistenceError: falha ao gravar o resultado (sucesso OU falha) no banco.
    - InvalidFilenameError (services.batch_filenames): `filename` inválido — levantada ANTES de
      qualquer reserva, nenhuma cota é tocada.

    `on_progress(stage, percent)` (opcional, barra de progresso real): chamado com
    `stage="download"` uma vez, `percent=None` sempre (o yt-dlp roda com `noprogress=True` nesta
    etapa – de propósito, ver a auditoria; nunca um percentual inventado para o download), e com
    `stage="processing"` repetidamente durante o FFmpeg, com o percentual REAL vindo de
    processor/ffmpeg.py (ou `None` se a duração do vídeo de entrada não for conhecida). Não afeta
    em nada a quota, o status da geração, a limpeza dos temporários nem o tratamento de exceções
    já existentes — é só um canal de leitura a mais, opcional.

    A partir da Etapa 9.3, o corpo desta função (depois da reserva) é o mesmo `_execute_reserved`
    reaproveitado pela orquestração de lote (`run_batch`, abaixo) — ver o comentário daquela
    função. Nenhum comportamento desta função mudou: a extração só move código, não altera ordem,
    exceções nem efeitos colaterais.

    `filename` (opcional, 02/10/2026 -- mesmo campo que o item de lote já tinha): sanitizado
    AQUI, pela mesma função usada no lote (services.batch_filenames.sanitize_batch_filename),
    ANTES de qualquer reserva -- um nome inválido levanta InvalidFilenameError sem consumir cota,
    igual à regra já aplicada ao lote (routes/generations.py). O valor sanitizado é gravado em
    Generation.display_filename (reserve_generation) e é o que o download individual já usa."""
    validated = validate_url(url)
    platform: Platform = detect_platform(validated.url)
    display_filename = sanitize_batch_filename(filename)

    # services.usage.reserve_generation é a ÚNICA fonte de reserva (Etapa 4): nenhum contador
    # paralelo é criado aqui. Pode levantar QuotaExceededError/EntitlementInconsistencyError —
    # nesse ponto nada foi baixado ainda, então não há nada para limpar.
    reservation = reserve_generation(
        db, user_id=user.id, request_id=uuid.uuid4().hex, platform=platform.value,
        display_filename=display_filename,
    )
    outcome = _execute_reserved(db, generation_id=reservation.generation_id, url=validated.url, on_progress=on_progress)
    logger.info(
        "[GENERATION] concluída id=%s user_id=%s plataforma=%s bytes=%s",
        outcome.generation_id, user.id, outcome.platform, outcome.size_bytes,
    )
    return outcome


def _execute_reserved(
    db: Session, *, generation_id: int, url: str,
    on_progress: Callable[[str, float | None], None] | None = None,
) -> GenerationOutcome:
    """Núcleo reutilizável (Etapa 9.3): download -> processamento -> storage -> complete/fail,
    para uma Generation JÁ RESERVADA (`generation_id`). Não reserva nada, não conhece usuário nem
    plano -- quem chama já reservou antes (generate_from_url, acima, para a geração avulsa; ou
    reserve_batch + run_batch, mais abaixo, para um item de lote). Extraído de generate_from_url
    SEM NENHUMA mudança de comportamento: mesma ordem, mesmas exceções, mesma limpeza.

    Chamar validate_url/detect_platform de novo aqui (já feitos uma vez por quem reservou, no caso
    da geração avulsa) é intencional e inofensivo -- o mesmo padrão que download_video já tem
    internamente (também revalida a URL); nenhuma chamada de rede acontece nesta validação."""
    validated = validate_url(url)
    platform: Platform = detect_platform(validated.url)

    download_path: Path | None = None
    output_path: Path | None = None
    storage_key: str | None = None
    try:
        try:
            if on_progress is not None:
                # Sem percentual real disponível nesta fase (yt-dlp com noprogress=True) — a UI
                # deve tratar isto como estado indeterminado, nunca um número fingido.
                on_progress("download", None)
            download_result = download_video(validated.url)
            download_path = download_result.temp_path

            def _on_processing_progress(percent: float | None) -> None:
                if on_progress is not None:
                    on_progress("processing", percent)

            processing_result = process_video(download_result.temp_path, on_progress=_on_processing_progress)
            output_path = processing_result.output_path

            # result_storage.save() fica no MESMO try de download/processamento de propósito: uma
            # falha aqui (ex.: disco cheio) deve ser tratada exatamente como uma falha de
            # download/processamento — fail_generation, cota liberada — sem precisar de um except
            # dedicado. Depois do save(), output_path já não existe mais (foi renomeado para o
            # storage); _cleanup_quietly no finally continua seguro (só apaga se o path existir).
            storage_key = result_storage.save(output_path, size_bytes=processing_result.size_bytes)
        except (DownloadError, ProcessorError) as exc:
            _mark_failed(db, generation_id, exc)
            raise
        except Exception as exc:
            # Qualquer falha inesperada TAMBÉM libera a cota: a geração nunca fica presa em
            # "reserved" por causa de um erro que as camadas de baixo não previram.
            logger.error(
                "[GENERATION] falha inesperada na geração %s: %s",
                generation_id, exc.__class__.__name__, exc_info=True,
            )
            _mark_failed(db, generation_id, exc)
            raise

        # Um único `now`, usado tanto em finished_at (dentro de complete_generation) quanto no
        # cálculo de output_expires_at — para os dois baterem exatamente (finished_at + TTL), sem
        # depender de duas leituras de relógio separadas.
        now = resolve_now()
        output_expires_at = now + timedelta(seconds=RESULT_TTL_SECONDS)
        try:
            complete_generation(
                db, generation_id,
                output_sha256=processing_result.output_sha256,
                duration_ms=round(processing_result.duration_seconds * 1000),
                output_size_bytes=processing_result.size_bytes,
                output_expires_at=output_expires_at,
                output_storage_key=storage_key,
                now=now,
            )
        except Exception as persistence_exc:
            # O vídeo FOI processado (e salvo) com sucesso: isto não é uma falha de
            # download/processamento, é uma falha de gravação. Não chamamos fail_generation aqui
            # (marcar como "failed" seria uma afirmação falsa sobre um processamento que deu
            # certo); a rede de segurança já existente (fail_stale_reservations, Etapa 4) cobre
            # uma reserva presa por isso. O arquivo já salvo no storage, porém, ficaria órfão (sem
            # nenhuma linha do banco apontando para ele, já que a gravação falhou) — removido aqui
            # como limpeza best-effort, sem NUNCA mascarar o erro original de persistência.
            logger.error(
                "[GENERATION] complete_generation falhou para %s: %s",
                generation_id, persistence_exc.__class__.__name__, exc_info=True,
            )
            try:
                result_storage.delete(storage_key)
            except Exception:
                logger.warning(
                    "[GENERATION] falha ao limpar arquivo órfão do storage (geração %s)",
                    generation_id, exc_info=True,
                )
            raise GenerationPersistenceError(generation_id, "complete", persistence_exc) from persistence_exc
    finally:
        _cleanup_quietly(download_path, output_path)

    # O log de conclusão (com user_id) fica em generate_from_url, que é quem conhece o usuário; a
    # orquestração de lote (run_batch) faz o seu próprio log por item, abaixo.
    return GenerationOutcome(
        generation_id=generation_id,
        status="completed",
        platform=platform.value,
        size_bytes=processing_result.size_bytes,
        duration_seconds=processing_result.duration_seconds,
        output_sha256=processing_result.output_sha256,
    )


# ------------------------------------------------------------------------------ orquestração de lote (Etapa 9.3)
@dataclass(frozen=True)
class BatchItemOutcome:
    """Resultado de UM item de lote depois de run_batch. `outcome` é None quando `ok` é False;
    `error_code` é None quando `ok` é True. `position` é 1-based, na mesma ordem da lista `items`
    passada para run_batch (que por sua vez deve ser a mesma ordem de `BatchReservation.generation_ids`,
    ver routes/generations.py)."""

    position: int
    generation_id: int
    ok: bool
    outcome: GenerationOutcome | None
    error_code: str | None


def run_batch(
    db: Session,
    *,
    items: list[tuple[int, str]],
    on_item_progress: Callable[[int, int, str, float | None], None] | None = None,
) -> list[BatchItemOutcome]:
    """Processa, SEQUENCIALMENTE (download/FFmpeg são pesados; nenhum paralelismo aqui, mesma
    postura de download/processor), cada item `(generation_id, url)` de um lote JÁ RESERVADO por
    services.usage.reserve_batch — não reserva nada, não cria nenhuma Generation.

    Reaproveita `_execute_reserved` (o MESMO núcleo da geração avulsa) para cada item: não duplica
    download, processamento, storage nem a lógica de marcar falha/sucesso.

    Isolamento de falhas (regra explícita da Etapa 9.3): uma falha de ITEM (DownloadError,
    ProcessorError, ou qualquer exceção inesperada) NUNCA interrompe os demais itens -- é
    capturada aqui (a própria `_execute_reserved` já marcou a Generation como failed e liberou a
    cota de VÍDEO só daquele item) e a orquestração segue para o próximo. A cota de OPERAÇÃO de
    lote (Etapa 9.2) não é tocada por nada nesta função -- já foi consumida atomicamente por
    reserve_batch, antes de run_batch ser chamada, e nunca é devolvida por uma falha de item.

    Exceção: GenerationPersistenceError (falha ao GRAVAR no banco, depois do vídeo já processado)
    é uma falha de INFRAESTRUTURA, não do vídeo -- é propagada (relançada), abortando o restante do
    lote, em vez de ser tratada como "só este item falhou" (não adianta continuar processando mais
    itens se o banco está recusando gravações).

    Reexecução idempotente (o mesmo request_id de lote é reenviado): para um item cuja Generation
    JÁ NÃO está mais "reserved" (completed ou failed de uma tentativa anterior -- ex.: o processo
    caiu no meio do lote e foi reenviado), run_batch NÃO reprocessa (não baixa/gera de novo) --
    apenas relata o estado já gravado, exatamente como reserve_generation já faz para o request_id
    repetido da geração avulsa (nunca reexecuta um efeito colateral que já aconteceu)."""
    resultados: list[BatchItemOutcome] = []
    total = len(items)
    for position, (generation_id, url) in enumerate(items, start=1):
        generation = db.get(Generation, generation_id)
        if generation is not None and generation.status != "reserved":
            if generation.status == "completed":
                outcome = GenerationOutcome(
                    generation_id=generation.id,
                    status="completed",
                    platform=generation.platform or "",
                    size_bytes=generation.output_size_bytes or 0,
                    duration_seconds=(generation.duration_ms or 0) / 1000,
                    output_sha256=generation.output_sha256 or "",
                )
                resultados.append(BatchItemOutcome(position, generation_id, True, outcome, None))
            else:  # "failed"
                resultados.append(BatchItemOutcome(position, generation_id, False, None, generation.error_code))
            continue

        def _progress(stage: str, percent: float | None, _position=position, _gid=generation_id) -> None:
            if on_item_progress is not None:
                on_item_progress(_position, _gid, stage, percent)

        try:
            outcome = _execute_reserved(db, generation_id=generation_id, url=url, on_progress=_progress)
            resultados.append(BatchItemOutcome(position, generation_id, True, outcome, None))
        except GenerationPersistenceError:
            raise
        except Exception as exc:
            logger.error(
                "[GENERATION] item %s/%s do lote (geração %s) falhou: %s",
                position, total, generation_id, exc.__class__.__name__, exc_info=True,
            )
            resultados.append(BatchItemOutcome(position, generation_id, False, None, _error_code_for(exc)))
    return resultados