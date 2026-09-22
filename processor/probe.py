"""Inspeção do arquivo com ffprobe: duração, presença de stream de vídeo, áudio opcional.

Nunca ativa o parâmetro de shell do subprocess nem usa os.system: sempre subprocess.run com uma
lista fixa de argumentos. O caminho do ffprobe vem de config.py (FFPROBE_PATH) — se for só um
nome de executável (sem barra), é resolvido
via shutil.which() (não assume um caminho fixo de Linux/Windows); se já for um caminho absoluto
configurado por ambiente, é usado como está.
"""
import json
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from config import get_settings
from processor.errors import FfprobeInvalidOutputError, FfprobeUnavailableError

logger = logging.getLogger("minhoca")

PROBE_TIMEOUT_SECONDS = 20  # ffprobe só lê metadados/cabeçalho: é rápido, mesmo em vídeos válidos


@dataclass(frozen=True)
class ProbeResult:
    duration_seconds: float | None
    has_video: bool
    has_audio: bool
    video_codec: str | None
    audio_codec: str | None


def resolve_executable(path_or_name: str, *, what: str, error_cls: type = FfprobeUnavailableError) -> str:
    """Nome de executável -> resolvido em PATH via shutil.which(). Caminho absoluto -> usado como
    está (nunca assumimos um caminho fixo de Linux/Windows: quem decide é a configuração).

    `error_cls` é a exceção levantada quando o binário não é encontrado — o padrão
    (FfprobeUnavailableError) serve para as chamadas deste próprio módulo; processor/ffmpeg.py
    passa FfmpegUnavailableError explicitamente, para que "FFmpeg ausente" e "ffprobe ausente"
    sejam diagnosticáveis como erros DIFERENTES, e não os dois sempre como o mesmo."""
    if os.path.isabs(path_or_name):
        if not os.path.isfile(path_or_name):
            raise error_cls(f"{what} configurado não existe: {path_or_name!r}")
        return path_or_name
    resolved = shutil.which(path_or_name)
    if resolved is None:
        raise error_cls(f"{what} não encontrado no PATH: {path_or_name!r}")
    return resolved


def _run_ffprobe(path: Path) -> dict:
    settings = get_settings()
    executable = resolve_executable(settings.ffprobe_path, what="ffprobe")
    args = [
        executable,
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    try:
        completed = subprocess.run(  # noqa: S603 - lista fixa de argumentos, nunca shell
            args, capture_output=True, timeout=PROBE_TIMEOUT_SECONDS, check=False,
        )
    except FileNotFoundError:
        raise FfprobeUnavailableError(f"ffprobe não pôde ser executado: {executable!r}") from None
    except subprocess.TimeoutExpired:
        raise FfprobeInvalidOutputError("ffprobe não respondeu a tempo") from None

    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace")[:500]
        logger.warning("[PROCESSOR] ffprobe falhou (código %s): %s", completed.returncode, stderr)
        raise FfprobeInvalidOutputError(f"ffprobe código {completed.returncode}")

    try:
        return json.loads(completed.stdout.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        raise FfprobeInvalidOutputError("ffprobe não devolveu JSON válido") from None


def probe(path: Path) -> ProbeResult:
    """Executa o ffprobe em `path` e devolve um resumo. Levanta FfprobeUnavailableError ou
    FfprobeInvalidOutputError. NÃO valida limites (duração/tamanho) — isso é processor/validation.py."""
    data = _run_ffprobe(path)
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    duration_raw = (data.get("format") or {}).get("duration")
    try:
        duration = float(duration_raw) if duration_raw is not None else None
    except (TypeError, ValueError):
        duration = None

    return ProbeResult(
        duration_seconds=duration,
        has_video=video is not None,
        has_audio=audio is not None,
        video_codec=(video or {}).get("codec_name"),
        audio_codec=(audio or {}).get("codec_name"),
    )
