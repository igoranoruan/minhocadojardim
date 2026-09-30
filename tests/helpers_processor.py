"""Fixtures sintéticos de vídeo para os testes da Etapa 6 (processor/).

Gerados em tempo de teste com o próprio FFmpeg (`-f lavfi`, ruído/padrão sintético) — nunca um
vídeo real baixado da internet. São pequenos e determinísticos: a mesma chamada sempre produz um
arquivo com as mesmas propriedades (duração, presença de stream, etc).
"""
import subprocess
from pathlib import Path

from config import get_settings


def _ffmpeg() -> str:
    return get_settings().ffmpeg_path


def make_video_with_audio(path: Path, *, duration: float = 1.0, size: str = "64x64") -> Path:
    subprocess.run(
        [
            _ffmpeg(), "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"testsrc=duration={duration}:size={size}:rate=5",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
            "-c:v", "libx264", "-c:a", "aac", "-y", str(path),
        ],
        check=True,
    )
    return path


def make_video_no_audio(path: Path, *, duration: float = 1.0, size: str = "64x64") -> Path:
    subprocess.run(
        [
            _ffmpeg(), "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"testsrc=duration={duration}:size={size}:rate=5",
            "-c:v", "libx264", "-y", str(path),
        ],
        check=True,
    )
    return path


def make_video_vp9_sem_audio(path: Path, *, duration: float = 1.0, size: str = "64x64") -> Path:
    """Vídeo em VP9 (nunca o caminho rápido/stream-copy -- só H.264 é considerado seguro para
    copy, ver processor/service.py::_COPY_SAFE_VIDEO_CODECS). Usado para provar que a
    otimização de performance cai automaticamente para a transcodificação normal quando o codec
    de entrada não é compatível."""
    subprocess.run(
        [
            _ffmpeg(), "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"testsrc=duration={duration}:size={size}:rate=5",
            "-c:v", "libvpx-vp9", "-y", str(path),
        ],
        check=True,
    )
    return path


def make_video_h264_com_audio_opus(path: Path, *, duration: float = 1.0, size: str = "64x64") -> Path:
    """Vídeo H.264 (copiável) com áudio Opus (NÃO copiável -- só AAC é seguro para `-c:a copy`,
    ver processor/service.py::_COPY_SAFE_AUDIO_CODECS). Usado para provar o caminho intermediário
    da otimização de performance (30/09, aprovação do CÉREBRO): vídeo copiado, só o áudio
    reencodado -- nunca o transcode completo (que reencodaria o vídeo à toa)."""
    subprocess.run(
        [
            _ffmpeg(), "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"testsrc=duration={duration}:size={size}:rate=5",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
            "-c:v", "libx264", "-c:a", "libopus", "-y", str(path),
        ],
        check=True,
    )
    return path


def make_audio_only(path: Path, *, duration: float = 1.0) -> Path:
    subprocess.run(
        [
            _ffmpeg(), "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
            "-c:a", "aac", "-y", str(path),
        ],
        check=True,
    )
    return path


def make_video_with_metadata(path: Path, *, duration: float = 1.0, tags: dict[str, str]) -> Path:
    """Vídeo com tags de metadata CONHECIDAS injetadas (title/artist/comment etc.), para provar
    que -map_metadata -1 realmente as remove — não basta checar a presença da flag no comando."""
    args = [
        _ffmpeg(), "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"testsrc=duration={duration}:size=64x64:rate=5",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
    ]
    for key, value in tags.items():
        args += ["-metadata", f"{key}={value}"]
    args += ["-c:v", "libx264", "-c:a", "aac", "-y", str(path)]
    subprocess.run(args, check=True)
    return path


def make_not_a_video(path: Path) -> Path:
    path.write_bytes(b"isto nao e um video, so bytes quaisquer")
    return path
