"""Execução do FFmpeg para EXTRAÇÃO DE ÁUDIO (Parte A/B, autorização do CÉREBRO, 11/10/2026):
MP3 CBR (libmp3lame, AUDIO_OUTPUT_BITRATE em config.py), sem metadados (-map_metadata -1) e sem
capa embutida (só o stream de áudio é mapeado, -map 0:a:0 -- nenhum stream de vídeo, inclusive uma
miniatura/capa embutida como "attached pic" de um MP3/M4A de origem, entra na saída).

Reaproveita por IMPORTAÇÃO (nunca duplica a lógica) a leitura de progresso/stderr e o kill seguro
já existentes em processor/ffmpeg.py -- `_read_stdout_progress`/`_read_stderr` só entendem o
protocolo `-progress pipe:1` do FFmpeg (chave=valor, genérico, não específico de vídeo) e
`_kill_and_wait` só garante que o processo morre e é colhido -- nenhum dos três sabe nada sobre
"-c:v"/"-c:a copy"/modos de vídeo. Escrito num arquivo PRÓPRIO, não dentro de processor/ffmpeg.py,
para não alterar um arquivo do fluxo de vídeo já testado (regra do CÉREBRO: não mexer no que já
funciona sem motivo técnico) -- só o comando/args e o classificador de falha são específicos de
áudio e vivem aqui.
"""
import logging
import queue
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

from config import AUDIO_OUTPUT_BITRATE, PROCESSING_TIMEOUT_SECONDS, get_settings
from processor.errors import FfmpegFailedError, FfmpegUnavailableError, ProcessingTimeoutError
from processor.ffmpeg import _STDERR_CAPTURE_LIMIT_BYTES, _kill_and_wait, _read_stderr, _read_stdout_progress
from processor.probe import resolve_executable

logger = logging.getLogger("minhoca")

_KILL_WAIT_SECONDS = 5
# Intervalo máximo que a thread principal fica bloqueada em queue.get() antes de reconferir o
# prazo do timeout -- mesmo valor/papel de processor/ffmpeg.py::_POLL_INTERVAL_SECONDS.
_POLL_INTERVAL_SECONDS = 0.5


def build_audio_args(*, executable: str, input_path: Path, output_path: Path) -> list[str]:
    """Só o stream de áudio (-map 0:a:0) é mapeado -- nenhum vídeo, nenhuma capa embutida entra na
    saída. -map_metadata -1 remove qualquer tag (título/artista/álbum/comentário) da origem.
    -c:a libmp3lame -b:a <bitrate>: MP3 em taxa de bits constante, conforme pedido do CÉREBRO."""
    return [
        executable,
        "-hide_banner",
        "-loglevel", "error",
        "-nostats",
        "-y",  # sobrescreve o arquivo de saída (nome gerado por nós, nunca existe antes)
        "-i", str(input_path),
        "-map", "0:a:0",
        "-map_metadata", "-1",
        "-c:a", "libmp3lame",
        "-b:a", AUDIO_OUTPUT_BITRATE,
        "-progress", "pipe:1",
        "-f", "mp3",
        str(output_path),
    ]


def _classify_audio_failure(stderr_text: str, returncode: int) -> FfmpegFailedError:
    return FfmpegFailedError(f"FFmpeg (áudio) terminou com código {returncode}: {stderr_text[:300]}")


def run_ffmpeg_audio(
    *,
    input_path: Path,
    output_path: Path,
    duration_seconds: float | None = None,
    on_progress: Callable[[float | None], None] | None = None,
) -> None:
    """Mesmo desenho de robustez de processor.ffmpeg.run_ffmpeg (watchdog de timeout real, duas
    threads de leitura, nunca deixa processo órfão) -- ver aquele módulo para a justificativa
    completa de cada decisão (idêntica aqui, só o comando montado é diferente)."""
    settings = get_settings()
    executable = resolve_executable(settings.ffmpeg_path, what="FFmpeg", error_cls=FfmpegUnavailableError)
    args = build_audio_args(executable=executable, input_path=input_path, output_path=output_path)

    try:
        process = subprocess.Popen(  # noqa: S603 - lista fixa de argumentos, nunca shell
            args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
    except FileNotFoundError:
        raise FfmpegUnavailableError(f"FFmpeg não pôde ser executado: {executable!r}") from None

    result_queue: queue.Queue = queue.Queue()
    thread_stdout = threading.Thread(
        target=_read_stdout_progress, args=(process.stdout, result_queue, duration_seconds, on_progress), daemon=True,
    )
    thread_stderr = threading.Thread(target=_read_stderr, args=(process.stderr, result_queue), daemon=True)
    thread_stdout.start()
    thread_stderr.start()

    prazo = time.monotonic() + PROCESSING_TIMEOUT_SECONDS
    stdout_concluido = False
    stderr_texto: str | None = None
    estourou = False
    while not (stdout_concluido and stderr_texto is not None):
        restante = prazo - time.monotonic()
        if restante <= 0:
            estourou = True
            break
        try:
            tipo, valor = result_queue.get(timeout=min(restante, _POLL_INTERVAL_SECONDS))
        except queue.Empty:
            continue
        if tipo == "stdout_eof":
            stdout_concluido = True
        elif tipo == "stderr_done":
            stderr_texto = valor

    if estourou:
        _kill_and_wait(process)
        thread_stdout.join(timeout=_KILL_WAIT_SECONDS)
        thread_stderr.join(timeout=_KILL_WAIT_SECONDS)
        logger.warning("[PROCESSOR_AUDIO] FFmpeg excedeu o timeout de %ss e foi encerrado", PROCESSING_TIMEOUT_SECONDS)
        raise ProcessingTimeoutError(f"FFmpeg excedeu {PROCESSING_TIMEOUT_SECONDS}s") from None

    process.wait()  # as duas threads já deram EOF -- o processo já terminou; só coleta o returncode

    if process.returncode != 0:
        stderr_text = (stderr_texto or "")[:_STDERR_CAPTURE_LIMIT_BYTES]
        logger.warning("[PROCESSOR_AUDIO] FFmpeg falhou (código %s): %s", process.returncode, stderr_text)
        raise _classify_audio_failure(stderr_text, process.returncode)
