"""Monta e executa o comando FFmpeg da V1: MP4 H.264 + AAC (quando há áudio), metadados e
capítulos removidos, faststart, sem legendas/streams de dados na saída.

Segurança: nunca ativar o parâmetro de shell do subprocess, nunca usar os.system, nunca montar o
comando como string — sempre uma lista fixa de argumentos passada ao subprocess. Os dois únicos
valores externos que entram na lista são os CAMINHOS de entrada/saída (gerados por
processor/tempfiles.py, nunca por dado do usuário) — nenhum dado de fora vira FLAG do FFmpeg.

Timeout: Popen -> wait(timeout) -> kill() -> wait(). Isso garante que nenhum processo fica órfão:
mesmo no timeout, o processo é encerrado e aguardado antes desta função devolver o controle.
"""
import logging
import subprocess
import time
from pathlib import Path

from config import PROCESSING_TIMEOUT_SECONDS, get_settings
from processor.errors import FfmpegFailedError, FfmpegUnavailableError, ProcessingTimeoutError, ProcessorError
from processor.probe import resolve_executable

logger = logging.getLogger("minhoca")

# Limite do que guardamos do stderr do FFmpeg (log-only; nunca vai para o usuário). Processos que
# travam produzindo log não devem crescer sem controle em memória.
_STDERR_CAPTURE_LIMIT_BYTES = 8 * 1024
# Intervalo entre tentativas de ler o processo depois de kill(), antes do wait() final bloqueante.
_KILL_WAIT_SECONDS = 5


def build_args(*, executable: str, input_path: Path, output_path: Path, has_audio: bool) -> list[str]:
    """Lista de argumentos do FFmpeg. `has_audio` decide TODO o tratamento de áudio: se False,
    nenhum `-map` nem `-c:a` de áudio entram na lista (saída só com vídeo; nada de áudio artificial)."""
    args = [
        executable,
        "-hide_banner",
        "-loglevel", "error",
        "-y",  # sobrescreve o arquivo de saída (nome gerado por nós, nunca existe antes)
        "-i", str(input_path),
        "-map", "0:v:0",
    ]
    if has_audio:
        args += ["-map", "0:a:0"]
    args += [
        "-map_metadata", "-1",
        "-map_chapters", "-1",
        "-c:v", "libx264",
    ]
    if has_audio:
        args += ["-c:a", "aac"]
    args += [
        "-movflags", "+faststart",
        "-f", "mp4",
        str(output_path),
    ]
    return args


_UNKNOWN_ENCODER_MARKERS = ("unknown encoder", "encoder not found")


def _classify_failure(stderr_text: str, returncode: int) -> ProcessorError:
    lowered = stderr_text.lower()
    if any(marker in lowered for marker in _UNKNOWN_ENCODER_MARKERS) and "libx264" in lowered:
        return FfmpegUnavailableError(f"encoder libx264 indisponível no FFmpeg (código {returncode})")
    return FfmpegFailedError(f"FFmpeg terminou com código {returncode}: {stderr_text[:300]}")


def run_ffmpeg(*, input_path: Path, output_path: Path, has_audio: bool) -> None:
    """Executa o FFmpeg com timeout real. Levanta FfmpegUnavailableError, FfmpegFailedError ou
    ProcessingTimeoutError. NUNCA deixa o processo órfão: no timeout, mata e aguarda antes de
    devolver o controle ao chamador."""
    settings = get_settings()
    executable = resolve_executable(settings.ffmpeg_path, what="FFmpeg", error_cls=FfmpegUnavailableError)
    args = build_args(executable=executable, input_path=input_path, output_path=output_path, has_audio=has_audio)

    try:
        process = subprocess.Popen(  # noqa: S603 - lista fixa de argumentos, nunca shell
            args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
    except FileNotFoundError:
        raise FfmpegUnavailableError(f"FFmpeg não pôde ser executado: {executable!r}") from None

    try:
        _, stderr = process.communicate(timeout=PROCESSING_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        _kill_and_wait(process)
        logger.warning("[PROCESSOR] FFmpeg excedeu o timeout de %ss e foi encerrado", PROCESSING_TIMEOUT_SECONDS)
        raise ProcessingTimeoutError(f"FFmpeg excedeu {PROCESSING_TIMEOUT_SECONDS}s") from None

    if process.returncode != 0:
        stderr_text = stderr[:_STDERR_CAPTURE_LIMIT_BYTES].decode("utf-8", errors="replace")
        logger.warning("[PROCESSOR] FFmpeg falhou (código %s): %s", process.returncode, stderr_text)
        raise _classify_failure(stderr_text, process.returncode)


def _kill_and_wait(process: subprocess.Popen) -> None:
    """Garante que o processo morre e é colhido (evita zumbi), mesmo se kill() já não puder mais
    afetá-lo (processo que terminou entre o timeout e esta chamada)."""
    process.kill()
    try:
        process.communicate(timeout=_KILL_WAIT_SECONDS)
    except subprocess.TimeoutExpired:
        # Situação extrema (processo preso em I/O não interrompível): registra e ainda assim
        # tenta colher o retorno uma última vez, sem travar indefinidamente o chamador.
        logger.error("[PROCESSOR] FFmpeg não respondeu a kill() em %ss", _KILL_WAIT_SECONDS)
        process.wait(timeout=_KILL_WAIT_SECONDS)
