"""Orquestração da geração individual: URL -> reserva -> download -> processamento -> conclusão.

Fluxo obrigatório (autorização da Etapa 7):

    validar URL -> detectar plataforma -> reservar 1 geração -> download -> processamento
    -> complete_generation -> limpeza dos temporários -> devolver GenerationOutcome

Este módulo NÃO importa routes/, NÃO conhece HTTP e NÃO cria nenhum contador de uso próprio —
usa exclusivamente services.usage.reserve_generation/complete_generation/fail_generation, que já
são a única fonte de consumo (Etapa 4). Reaproveita download.service.download_video (Etapa 5) e
processor.service.process_video (Etapa 6) sem alterar nenhum dos dois.

Este módulo não traduz nada para HTTP: deixa propagar as exceções já existentes das camadas
reaproveitadas (DownloadError, ProcessorError, UsageError, EntitlementError) e define só
GenerationPersistenceError, para o caso novo desta etapa (falha ao GRAVAR o resultado, depois do
download/processamento já terem terminado). A tradução para HTTP é feita em routes/generations.py.
"""
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from database.models import User
from download.errors import DownloadError
from download.platform import Platform, detect_platform
from download.service import download_video
from download.url_safety import validate_url
from processor.errors import ProcessorError
from processor.service import process_video
from services.usage import complete_generation, fail_generation, reserve_generation

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


def generate_from_url(db: Session, *, user: User, url: str) -> GenerationOutcome:
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
    """
    validated = validate_url(url)
    platform: Platform = detect_platform(validated.url)

    # services.usage.reserve_generation é a ÚNICA fonte de reserva (Etapa 4): nenhum contador
    # paralelo é criado aqui. Pode levantar QuotaExceededError/EntitlementInconsistencyError —
    # nesse ponto nada foi baixado ainda, então não há nada para limpar.
    reservation = reserve_generation(
        db, user_id=user.id, request_id=uuid.uuid4().hex, platform=platform.value,
    )
    generation_id = reservation.generation_id

    download_path: Path | None = None
    output_path: Path | None = None
    try:
        try:
            download_result = download_video(validated.url)
            download_path = download_result.temp_path

            processing_result = process_video(download_result.temp_path)
            output_path = processing_result.output_path
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

        try:
            complete_generation(
                db, generation_id,
                output_sha256=processing_result.output_sha256,
                duration_ms=round(processing_result.duration_seconds * 1000),
            )
        except Exception as persistence_exc:
            # O vídeo FOI processado com sucesso: isto não é uma falha de download/processamento,
            # é uma falha de gravação. Não chamamos fail_generation aqui (marcar como "failed"
            # seria uma afirmação falsa sobre um processamento que deu certo); a rede de segurança
            # já existente (fail_stale_reservations, Etapa 4) cobre uma reserva presa por isso.
            logger.error(
                "[GENERATION] complete_generation falhou para %s: %s",
                generation_id, persistence_exc.__class__.__name__, exc_info=True,
            )
            raise GenerationPersistenceError(generation_id, "complete", persistence_exc) from persistence_exc
    finally:
        _cleanup_quietly(download_path, output_path)

    logger.info(
        "[GENERATION] concluída id=%s user_id=%s plataforma=%s bytes=%s",
        generation_id, user.id, platform.value, processing_result.size_bytes,
    )
    return GenerationOutcome(
        generation_id=generation_id,
        status="completed",
        platform=platform.value,
        size_bytes=processing_result.size_bytes,
        duration_seconds=processing_result.duration_seconds,
        output_sha256=processing_result.output_sha256,
    )
