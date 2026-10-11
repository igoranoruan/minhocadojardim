"""Ponto de entrada da EXTRAÇÃO DE ÁUDIO (Parte A/B, autorização do CÉREBRO, 11/10/2026).

Fluxo: arquivo de entrada (vídeo OU áudio puro, vindo de download/audio_service.py) -> validação
própria (processor/audio_validation.py, SEM exigir stream de vídeo) -> FFmpeg (MP3, sem metadados,
processor/ffmpeg_audio.py) -> validação de saída -> SHA-256 -> AudioProcessingResult. Mesmo desenho
de processor/service.py::process_video, sem nenhuma das otimizações de "caminho rápido" (stream
copy) daquele módulo -- não se aplicam aqui: SEMPRE reencodamos para MP3 (nunca existe um "MP3 já
no codec exato" para copiar sem reencodar)."""
import hashlib
import logging
from collections.abc import Callable
from pathlib import Path

from processor.audio_result import AudioProcessingResult
from processor.audio_validation import validate_audio_input, validate_audio_output
from processor.errors import ProcessorError
from processor.ffmpeg_audio import run_ffmpeg_audio
from processor.tempfiles import cleanup, new_temp_stub

logger = logging.getLogger("minhoca")

_SHA256_CHUNK_SIZE = 1024 * 1024  # lê o arquivo final em pedaços para o hash -- nunca tudo em RAM


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_SHA256_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def process_audio(
    input_path: Path, *, on_progress: Callable[[float | None], None] | None = None,
) -> AudioProcessingResult:
    """Processa `input_path` (fonte já baixada, download.audio_service.download_audio_source) e
    devolve um MP3 validado. Levanta ProcessorError (ou subclasse, inclusive NoAudioStreamError:
    "Esse vídeo não tem áudio") em qualquer falha; nesse caso, todo arquivo temporário de SAÍDA já
    criado é removido antes de propagar o erro. O arquivo de ENTRADA nunca é apagado por esta
    camada -- quem o criou decide sobre ele (mesmo contrato de processor/service.py::process_video)."""
    input_probe = validate_audio_input(input_path)  # NoAudioStreamError/InputTooLargeError/... propagam
    stub = new_temp_stub()
    output_path = stub.with_suffix(".mp3")

    try:
        run_ffmpeg_audio(
            input_path=input_path, output_path=output_path,
            duration_seconds=input_probe.duration_seconds, on_progress=on_progress,
        )
        output_probe = validate_audio_output(output_path)
        output_size = output_path.stat().st_size
        output_sha256 = _sha256_of(output_path)  # só DEPOIS da validação final (nunca antes)
        logger.info(
            "[PROCESSOR_AUDIO] concluído entrada=%s saida=%s bytes=%s duracao=%ss",
            input_path.name, output_path.name, output_size, output_probe.duration_seconds,
        )
        return AudioProcessingResult(
            output_path=output_path,
            size_bytes=output_size,
            duration_seconds=output_probe.duration_seconds or 0.0,
            output_sha256=output_sha256,
        )
    except ProcessorError:
        cleanup(stub)
        raise
    except Exception as exc:
        cleanup(stub)
        logger.error("[PROCESSOR_AUDIO] falha inesperada tipo=%s", exc.__class__.__name__, exc_info=True)
        raise ProcessorError() from None
