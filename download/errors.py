"""Erros padronizados da camada de download.

Toda falha desta camada é uma instância de DownloadError (ou subclasse). Cada uma carrega uma
mensagem GENÉRICA (`user_message`), segura para mostrar ao usuário, e o detalhe técnico vai só
para o log de quem a levanta — nunca para o usuário (mesma regra da Etapa 3: mensagem pro usuário
≠ detalhe no log). Nenhuma mensagem aqui deve conter a URL completa, cookies, tokens ou segredos.
"""

GENERIC_USER_MESSAGE = "Não foi possível processar este vídeo agora."


class DownloadError(Exception):
    """Base de todos os erros da camada de download."""

    user_message = GENERIC_USER_MESSAGE

    def __init__(self, detail: str | None = None) -> None:
        self.detail = detail or self.user_message
        super().__init__(self.detail)


class InvalidUrlError(DownloadError):
    """URL mal formada, esquema não permitido, ou host ausente."""

    user_message = "O link informado não é válido."


class UnsupportedPlatformError(DownloadError):
    """O domínio da URL não é TikTok, Instagram, Pinterest nem YouTube."""

    user_message = "Esta plataforma ainda não é suportada."


class SsrfBlockedError(DownloadError):
    """A URL (ou um dos seus redirecionamentos) aponta para um destino interno/privado."""

    user_message = "O link informado não pode ser processado."


class TooManyRedirectsError(DownloadError):
    user_message = "O link informado não pode ser processado."


class DownloadTimeoutError(DownloadError):
    user_message = "O download demorou demais e foi cancelado."


class VideoTooLargeError(DownloadError):
    user_message = "O vídeo é maior do que o permitido."


class VideoTooLongError(DownloadError):
    user_message = "O vídeo é mais longo do que o permitido."


class DownloadFailedError(DownloadError):
    """Falha genérica da plataforma/ferramenta de extração (yt-dlp)."""

    user_message = GENERIC_USER_MESSAGE


class InvalidFileError(DownloadError):
    """O arquivo baixado não existe, está vazio, ou não é um vídeo válido."""

    user_message = GENERIC_USER_MESSAGE
