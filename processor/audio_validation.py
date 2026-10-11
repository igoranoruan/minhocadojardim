"""Validação de entrada/saída do pipeline de EXTRAÇÃO DE ÁUDIO (Parte A/B, autorização do CÉREBRO,
11/10/2026) -- separado de processor/validation.py de propósito: aquele módulo EXIGE um stream de
vídeo (NoVideoStreamError), o que é ERRADO aqui -- a fonte pode ser um áudio puro, vinda de
download/audio_service.py::download_audio_source com format "bestaudio" (sem nenhum stream de
vídeo, por natureza).

Reaproveita MAX_VIDEO_SIZE_BYTES/MAX_VIDEO_DURATION_SECONDS (os MESMOS limites técnicos já usados
pelo pipeline de vídeo) e processor/probe.py (ffprobe, sem nenhuma mudança) -- só a REGRA de quais
streams são obrigatórios muda: aqui é áudio que é obrigatório (NoAudioStreamError, "Esse vídeo não
tem áudio" -- texto exato pedido pelo CÉREBRO), vídeo é irrelevante.
"""
from pathlib import Path

from config import MAX_VIDEO_DURATION_SECONDS, MAX_VIDEO_SIZE_BYTES
from processor.errors import (
    FfprobeInvalidOutputError,
    InputTooLargeError,
    InputTooLongError,
    InvalidInputFileError,
    InvalidOutputFileError,
    NoAudioStreamError,
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


def validate_audio_input(path: Path) -> ProbeResult:
    """Mesma validação de tamanho/duração de processor.validation.validate_input, MAS sem exigir
    stream de vídeo -- exige stream de ÁUDIO (NoAudioStreamError) no lugar."""
    size = _file_size(path, InvalidInputFileError)
    if size > MAX_VIDEO_SIZE_BYTES:
        raise InputTooLargeError(f"{size} bytes > limite de {MAX_VIDEO_SIZE_BYTES} bytes")

    try:
        result = probe(path)
    except FfprobeInvalidOutputError:
        raise InvalidInputFileError("o arquivo não é uma mídia válida") from None
    if not result.has_audio:
        raise NoAudioStreamError("nenhum stream de áudio encontrado")
    if result.duration_seconds is not None and result.duration_seconds > MAX_VIDEO_DURATION_SECONDS:
        raise InputTooLongError(f"{result.duration_seconds}s > {MAX_VIDEO_DURATION_SECONDS}s")
    return result


def validate_audio_output(path: Path) -> ProbeResult:
    """Confere o MP3 gerado: existe, tamanho válido, dentro do limite, ffprobe reconhece um stream
    de ÁUDIO (a saída NUNCA deve ter stream de vídeo/capa embutida -- processor/ffmpeg_audio.py só
    mapeia `-map 0:a:0`, então um stream de vídeo aqui seria sinal de um bug de comando, nunca
    esperado de verdade)."""
    size = _file_size(path, InvalidOutputFileError)
    if size > MAX_VIDEO_SIZE_BYTES:
        raise OutputTooLargeError(f"{size} bytes > limite de {MAX_VIDEO_SIZE_BYTES} bytes")

    try:
        result = probe(path)
    except Exception as exc:
        raise InvalidOutputFileError(f"ffprobe não validou a saída: {exc.__class__.__name__}") from None
    if not result.has_audio:
        raise InvalidOutputFileError("saída sem stream de áudio")
    return result
