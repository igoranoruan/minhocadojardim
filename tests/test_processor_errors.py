"""Hierarquia de erros padronizados do processor."""
import pytest

from processor.errors import (
    FfmpegFailedError,
    FfmpegUnavailableError,
    FfprobeInvalidOutputError,
    FfprobeUnavailableError,
    GENERIC_USER_MESSAGE,
    InputTooLargeError,
    InputTooLongError,
    InvalidInputFileError,
    InvalidOutputFileError,
    NoVideoStreamError,
    OutputTooLargeError,
    ProcessingTimeoutError,
    ProcessorError,
)

SUBCLASSES = [
    InvalidInputFileError, InputTooLargeError, InputTooLongError, NoVideoStreamError,
    FfprobeUnavailableError, FfprobeInvalidOutputError, FfmpegUnavailableError, FfmpegFailedError,
    ProcessingTimeoutError, InvalidOutputFileError, OutputTooLargeError,
]


@pytest.mark.parametrize("exc_class", SUBCLASSES)
def test_toda_subclasse_e_um_processor_error(exc_class):
    assert issubclass(exc_class, ProcessorError)
    erro = exc_class("detalhe técnico interno")
    assert erro.detail == "detalhe técnico interno"
    assert isinstance(erro.user_message, str) and erro.user_message


def test_sem_detalhe_usa_a_mensagem_generica():
    assert FfmpegFailedError().detail == GENERIC_USER_MESSAGE


def test_mensagem_de_usuario_nao_vaza_stderr_bruto():
    erro = FfmpegFailedError("stderr bruto do ffmpeg: /caminho/interno/sensivel.mp4 falhou")
    assert "/caminho/interno" not in erro.user_message
