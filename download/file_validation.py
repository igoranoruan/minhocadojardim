"""Validação básica do arquivo baixado: existe, não está vazio, extensão permitida e dentro do
tamanho máximo. NÃO valida duração aqui (isso é ffprobe, que só entra oficialmente na Etapa 6 —
ver DownloadResult.duration_seconds em download/result.py).
"""
from pathlib import Path

from config import MAX_VIDEO_SIZE_BYTES
from download.errors import InvalidFileError, VideoTooLargeError

# Extensões que o yt-dlp pode produzir para vídeo (com merge_output_format=mp4, o caso comum é
# sempre .mp4; os outros ficam como rede de segurança para conteúdo que não passa por merge).
ALLOWED_EXTENSIONS = frozenset({"mp4", "mkv", "webm", "mov", "m4v"})


def validate_downloaded_file(path: Path) -> int:
    """Confere existência, extensão e tamanho. Devolve o tamanho em bytes.

    Levanta InvalidFileError/VideoTooLargeError e NÃO apaga o arquivo — a limpeza é sempre
    responsabilidade de quem orquestra (download/service.py chama download/tempfiles.cleanup).
    """
    if not path.exists() or not path.is_file():
        raise InvalidFileError(f"arquivo não encontrado: {path.name}")

    extension = path.suffix.lstrip(".").lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise InvalidFileError(f"extensão não permitida: {extension!r}")

    size = path.stat().st_size
    if size <= 0:
        raise InvalidFileError("arquivo vazio.")
    if size > MAX_VIDEO_SIZE_BYTES:
        raise VideoTooLargeError(f"{size} bytes > limite de {MAX_VIDEO_SIZE_BYTES} bytes")
    return size
