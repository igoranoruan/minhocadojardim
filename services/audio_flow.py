"""Orquestração da EXTRAÇÃO DE ÁUDIO avulsa (Parte A, autorização do CÉREBRO, 11/10/2026): URL ->
reserva -> download (áudio) -> conversão MP3 -> storage -> conclusão.

Espelha services/generation_flow.py (mesmo desenho: reserva -> download -> processamento -> storage
-> complete/fail), mas é um fluxo TOTALMENTE PARALELO e independente:
- nunca cria/toca uma linha em `generations` (não consome geração, pedido explícito do CÉREBRO) --
  usa sua PRÓPRIA tabela (`audio_extractions`, migration 0012) e serviço próprio
  (services/audio_usage.py), nunca services/usage.py.
- reaproveita o download (download/audio_service.py, que por sua vez reaproveita validação de
  URL/SSRF, plataforma e watchdog de download/service.py) e o processamento
  (processor/audio_service.py), escritos para esta autorização.
- NÃO altera nenhuma linha de services/generation_flow.py, services/usage.py, download/service.py
  nem processor/service.py -- são só reaproveitados/importados, nunca modificados.

Diferente de services/generation_flow.py, esta orquestração é SÍNCRONA (sem streaming SSE) --
decisão tomada nesta implementação porque o pedido não exige barra de progresso em tempo real para
áudio (ver routes/audio.py para a justificativa completa e a limitação documentada no relatório
final)."""
import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from config import RESULT_TTL_SECONDS
from database.models import User
from download.audio_service import download_audio_source
from download.errors import DownloadError
from download.platform import Platform, detect_platform
from download.url_safety import validate_url
from processor.audio_service import process_audio
from processor.errors import ProcessorError
from services import result_storage
from services.audio_usage import complete_audio_extraction, fail_audio_extraction, reserve_audio_extraction
from services.batch_filenames import sanitize_batch_filename
from utils.time_sp import resolve_now

logger = logging.getLogger("minhoca")

# Mesmo limite de services.generation_flow._ERROR_CODE_MAX_LENGTH, reaproveitado aqui só para
# cortar o nome da exceção com segurança -- não é uma regra nova, é o limite que já existe em
# services.audio_usage.MAX_ERROR_CODE_LENGTH.
_ERROR_CODE_MAX_LENGTH = 64
# Extensão fixa da extração de áudio -- MP3, sempre (processor/ffmpeg_audio.py nunca produz outra
# coisa). suffix passado a sanitize_batch_filename (services.batch_filenames) para o nome de
# exibição opcional nascer já com ".mp3", nunca ".mp4" (padrão do vídeo).
AUDIO_FILENAME_SUFFIX = ".mp3"
AUDIO_STORAGE_EXTENSION = "mp3"


@dataclass(frozen=True)
class AudioOutcome:
    """Resultado de uma extração de áudio concluída com sucesso."""

    extraction_id: int
    status: str  # sempre "completed" quando este objeto é devolvido
    platform: str
    size_bytes: int
    duration_seconds: float
    output_sha256: str


class AudioPersistenceError(Exception):
    """A gravação do resultado no banco falhou DEPOIS do download/processamento já terem
    terminado -- mesma categoria de services.generation_flow.GenerationPersistenceError (não é uma
    falha do áudio, é uma falha de persistência). `phase` diz em qual chamada isso aconteceu
    ("complete" ou "fail"), só para log; nunca é exposto ao usuário."""

    def __init__(self, extraction_id: int, phase: str, original: BaseException) -> None:
        self.extraction_id = extraction_id
        self.phase = phase
        self.original = original
        super().__init__(f"falha ao persistir a extração de áudio {extraction_id} (fase={phase}): {original!r}")


def _error_code_for(exc: BaseException) -> str:
    """Categoria curta para error_code, a partir do TIPO da exceção -- nunca a mensagem crua."""
    return exc.__class__.__name__[:_ERROR_CODE_MAX_LENGTH]


def _cleanup_quietly(*paths: Path | None) -> None:
    """Remove os arquivos temporários (download e/ou saída processada). Nunca levanta -- mesma
    regra de services.generation_flow._cleanup_quietly."""
    for path in paths:
        if path is None:
            continue
        try:
            if path.exists():
                path.unlink()
        except OSError:
            logger.warning("[AUDIO] falha ao remover arquivo temporário %s", path.name)


def _mark_failed(db: Session, extraction_id: int, original_exc: BaseException) -> None:
    """Marca a extração como FAILED (libera o limite diário). Se a própria gravação falhar, isso
    vira AudioPersistenceError -- nunca silenciada, mas também nunca confundida com a causa
    original (preservada como __cause__)."""
    try:
        fail_audio_extraction(db, extraction_id, error_code=_error_code_for(original_exc))
    except Exception as persistence_exc:
        logger.error(
            "[AUDIO] falha ao gravar FAILED da extração %s (causa original: %s): %s",
            extraction_id, original_exc.__class__.__name__, persistence_exc.__class__.__name__,
            exc_info=True,
        )
        raise AudioPersistenceError(extraction_id, "fail", persistence_exc) from original_exc


def execute_reserved_audio_extraction(
    db: Session, *, extraction_id: int, url: str,
    on_progress=None,
) -> AudioOutcome:
    """Núcleo reutilizável (mesmo papel de services.generation_flow._execute_reserved): download ->
    processamento -> storage -> complete/fail, para UMA AudioExtraction JÁ RESERVADA. Usado tanto
    pela extração avulsa (extract_audio_from_url, abaixo) quanto, na Parte B, por cada item do lote
    de áudio -- não reserva nada, não cria nenhuma AudioExtraction.

    `on_progress(stage, percent, platform)` (opcional): mesma assinatura de
    services.generation_flow -- não usado pela rota síncrona de Parte A (routes/audio.py não passa
    nenhum), mas mantido para reaproveitamento futuro sem mudar a assinatura."""
    validated = validate_url(url)
    platform: Platform = detect_platform(validated.url)

    download_path: Path | None = None
    output_path: Path | None = None
    storage_key: str | None = None
    try:
        try:
            if on_progress is not None:
                on_progress("download", None, platform.value)
            download_result = download_audio_source(validated.url)
            download_path = download_result.temp_path

            def _on_processing_progress(percent: float | None) -> None:
                if on_progress is not None:
                    on_progress("processing", percent, platform.value)

            if on_progress is not None:
                on_progress("processing", None, platform.value)
            processing_result = process_audio(download_result.temp_path, on_progress=_on_processing_progress)
            output_path = processing_result.output_path

            # result_storage.save() fica no MESMO try de download/processamento de propósito: uma
            # falha aqui (ex.: disco cheio) deve ser tratada exatamente como uma falha de
            # download/processamento -- fail_audio_extraction, limite diário liberado.
            storage_key = result_storage.save(
                output_path, size_bytes=processing_result.size_bytes, extension=AUDIO_STORAGE_EXTENSION,
            )
        except (DownloadError, ProcessorError) as exc:
            _mark_failed(db, extraction_id, exc)
            raise
        except Exception as exc:
            logger.error(
                "[AUDIO] falha inesperada na extração %s: %s",
                extraction_id, exc.__class__.__name__, exc_info=True,
            )
            _mark_failed(db, extraction_id, exc)
            raise

        now = resolve_now()
        output_expires_at = now + timedelta(seconds=RESULT_TTL_SECONDS)
        try:
            complete_audio_extraction(
                db, extraction_id,
                output_sha256=processing_result.output_sha256,
                duration_ms=round(processing_result.duration_seconds * 1000),
                output_size_bytes=processing_result.size_bytes,
                output_expires_at=output_expires_at,
                output_storage_key=storage_key,
                now=now,
            )
        except Exception as persistence_exc:
            logger.error(
                "[AUDIO] complete_audio_extraction falhou para %s: %s",
                extraction_id, persistence_exc.__class__.__name__, exc_info=True,
            )
            try:
                result_storage.delete(storage_key, AUDIO_STORAGE_EXTENSION)
            except Exception:
                logger.warning(
                    "[AUDIO] falha ao limpar arquivo órfão do storage (extração %s)",
                    extraction_id, exc_info=True,
                )
            raise AudioPersistenceError(extraction_id, "complete", persistence_exc) from persistence_exc
    finally:
        _cleanup_quietly(download_path, output_path)

    return AudioOutcome(
        extraction_id=extraction_id,
        status="completed",
        platform=platform.value,
        size_bytes=processing_result.size_bytes,
        duration_seconds=processing_result.duration_seconds,
        output_sha256=processing_result.output_sha256,
    )


def extract_audio_from_url(
    db: Session, *, user: User, url: str, filename: str | None = None, on_progress=None,
) -> AudioOutcome:
    """Executa o fluxo completo de UMA extração de áudio avulsa para `user`.

    Levanta, sem capturar (a tradução para HTTP é da rota):
    - DownloadError/subclasses (inclusive NoAudioInPostError): URL inválida, plataforma não
      suportada, SSRF bloqueado, post sem áudio, etc.
    - AudioUsageError/subclasses (services.audio_usage): plano não pago (403), limite diário
      esgotado (429), operação de áudio já em andamento (409) -- levantadas ANTES de qualquer
      download (nenhum arquivo é criado).
    - ProcessorError/subclasses (inclusive NoAudioStreamError, "Esse vídeo não tem áudio"): falha
      no processamento, depois da reserva -- a extração já foi marcada FAILED antes de relançar.
    - AudioPersistenceError: falha ao gravar o resultado (sucesso OU falha) no banco.
    - InvalidFilenameError (services.batch_filenames): `filename` inválido -- levantada ANTES de
      qualquer reserva, nenhum limite diário é tocado."""
    validated = validate_url(url)
    platform: Platform = detect_platform(validated.url)
    display_filename = sanitize_batch_filename(filename, suffix=AUDIO_FILENAME_SUFFIX)

    # services.audio_usage.reserve_audio_extraction é a ÚNICA fonte de reserva: nenhum contador
    # paralelo é criado aqui. Pode levantar AudioNotAllowedError/AudioConcurrentOperationError/
    # AudioQuotaExceededError -- nesse ponto nada foi baixado ainda, então não há nada para limpar.
    reservation = reserve_audio_extraction(
        db, user_id=user.id, request_id=f"audio-{uuid.uuid4().hex}",
        platform=platform.value, display_filename=display_filename,
    )
    outcome = execute_reserved_audio_extraction(
        db, extraction_id=reservation.extraction_id, url=validated.url, on_progress=on_progress,
    )
    logger.info(
        "[AUDIO] extração concluída id=%s user_id=%s plataforma=%s bytes=%s",
        outcome.extraction_id, user.id, outcome.platform, outcome.size_bytes,
    )
    return outcome
