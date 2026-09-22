"""Validação de arquivos de entrada e saída (tamanho, existência, streams).

Reaproveita MAX_VIDEO_SIZE_BYTES e MAX_VIDEO_DURATION_SECONDS, já definidas na Etapa 5 — são os
MESMOS limites do produto, agora conferidos de verdade contra o arquivo real (via ffprobe), não
só contra o que a plataforma informou.
"""
from pathlib import Path

from config import MAX_VIDEO_DURATION_SECONDS, MAX_VIDEO_SIZE_BYTES
from processor.errors import (
    FfprobeInvalidOutputError,
    InputTooLargeError,
    InputTooLongError,
    InvalidInputFileError,
    InvalidOutputFileError,
    NoVideoStreamError,
    OutputTooLargeError,
)
from processor.probe import ProbeResult, probe


def _file_size(path: Path, error_cls) -> int:
    if not path.exists() or not path.is_file():
        raise error_cls(f"arquivo não encontrado: {path.name}")
    size = path.stat().st_size
    if size <= 0:
        raise error_cls("arquivo vazio.")
    return size


def validate_input(path: Path) -> ProbeResult:
    """Confere existência/tamanho/duração/stream de vídeo do arquivo de ENTRADA (o que a Etapa 5
    baixou). Áudio é OPCIONAL — não é exigido aqui. Devolve o ProbeResult para reaproveitar (evita
    rodar o ffprobe duas vezes)."""
    size = _file_size(path, InvalidInputFileError)
    if size > MAX_VIDEO_SIZE_BYTES:
        raise InputTooLargeError(f"{size} bytes > limite de {MAX_VIDEO_SIZE_BYTES} bytes")

    try:
        result = probe(path)  # FfprobeUnavailableError (binário ausente) propaga como está: é
        # problema de ambiente, não deste arquivo específico.
    except FfprobeInvalidOutputError:
        # O ffprobe rodou e não reconheceu o conteúdo como mídia válida: do ponto de vista de
        # quem chama, isso é "este arquivo de entrada é inválido", não uma falha do ffprobe em si.
        raise InvalidInputFileError("o arquivo não é um vídeo válido") from None
    if not result.has_video:
        raise NoVideoStreamError("nenhum stream de vídeo encontrado")
    if result.duration_seconds is not None and result.duration_seconds > MAX_VIDEO_DURATION_SECONDS:
        raise InputTooLongError(f"{result.duration_seconds}s > {MAX_VIDEO_DURATION_SECONDS}s")
    return result


def validate_output(path: Path) -> ProbeResult:
    """Confere o MP4 gerado pelo FFmpeg: existe, tem tamanho válido, dentro do limite, e o
    ffprobe reconhece nele um stream de vídeo."""
    size = _file_size(path, InvalidOutputFileError)
    if size > MAX_VIDEO_SIZE_BYTES:
        raise OutputTooLargeError(f"{size} bytes > limite de {MAX_VIDEO_SIZE_BYTES} bytes")

    try:
        result = probe(path)
    except Exception as exc:  # ffprobe indisponível aqui é a mesma falha de saída inválida
        raise InvalidOutputFileError(f"ffprobe não validou a saída: {exc.__class__.__name__}") from None
    if not result.has_video:
        raise InvalidOutputFileError("saída sem stream de vídeo")
    return result
