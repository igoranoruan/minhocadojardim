"""Hierarquia de erros padronizados da camada de download."""
import pytest

from download.errors import (
    DownloadError,
    DownloadFailedError,
    DownloadTimeoutError,
    GENERIC_USER_MESSAGE,
    InvalidFileError,
    InvalidUrlError,
    SsrfBlockedError,
    TooManyRedirectsError,
    UnsupportedPlatformError,
    VideoTooLargeError,
    VideoTooLongError,
)

SUBCLASSES = [
    InvalidUrlError, UnsupportedPlatformError, SsrfBlockedError, TooManyRedirectsError,
    DownloadTimeoutError, VideoTooLargeError, VideoTooLongError, DownloadFailedError, InvalidFileError,
]


@pytest.mark.parametrize("exc_class", SUBCLASSES)
def test_toda_subclasse_e_um_download_error(exc_class):
    assert issubclass(exc_class, DownloadError)
    erro = exc_class("detalhe técnico interno")
    assert isinstance(erro, DownloadError)
    assert erro.detail == "detalhe técnico interno"
    assert isinstance(erro.user_message, str) and erro.user_message


def test_sem_detalhe_usa_a_mensagem_generica_do_usuario():
    erro = DownloadFailedError()
    assert erro.detail == erro.user_message == GENERIC_USER_MESSAGE


def test_mensagens_de_usuario_nao_vazam_detalhe_tecnico():
    erro = SsrfBlockedError("host resolve para 10.0.0.5")
    assert "10.0.0.5" not in erro.user_message
    assert "10.0.0.5" in erro.detail  # o detalhe fica disponível só para quem loga
