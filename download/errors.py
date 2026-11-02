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


class ImageTooLargeError(DownloadError):
    """Suporte a imagem (Pinterest/Instagram sem vídeo, 03/10/2026)."""

    user_message = "A imagem é maior do que a permitida."


class ImageZipTooLargeError(ImageTooLargeError):
    """Carrossel do Instagram (vários slides, 03/10/2026 -- aprovação do CÉREBRO): o .zip final com
    todas as imagens passou de MAX_IMAGE_ZIP_SIZE_BYTES. Herda de ImageTooLargeError (mesma
    user_message, mesmo tratamento em download/service.py) -- só muda o detalhe técnico de log."""


class VideoTooLongError(DownloadError):
    user_message = "O vídeo é mais longo do que o permitido."


class DownloadFailedError(DownloadError):
    """Falha genérica da plataforma/ferramenta de extração (yt-dlp)."""

    user_message = GENERIC_USER_MESSAGE


class RestrictedContentError(DownloadError):
    """02/11/2026 (pedido do Igor -- 1 em cada 4 falhas do Instagram em produção): a plataforma
    recusou o post por RESTRIÇÃO DE PÚBLICO (conteúdo sensível/limite de idade), que só abre para
    quem está logado. Mensagens REAIS vistas nos logs: Instagram "This content isn't available to
    everyone: It can't be seen by certain audiences."; TikTok "This post may not be comfortable for
    some audiences. Log in for access." O KLANGO baixa sem login, então não há como baixar -- a
    mensagem explica isso em vez do genérico, para a pessoa não achar que é defeito do site."""

    user_message = (
        "Este vídeo tem restrição de público na plataforma (conteúdo sensível ou limite de idade) "
        "e só abre com login. Não conseguimos baixá-lo."
    )


class ContentNotAccessibleError(DownloadError):
    """02/11/2026: o Instagram devolveu "empty media response" (a própria mensagem do yt-dlp diz
    para conferir se o post abre SEM login) e o fallback de imagem também não achou nada na página
    pública -- o post é privado, tem restrição de público ou exige login. Ver download/service.py."""

    user_message = (
        "Não conseguimos acessar este post: ele pode ser privado, ter restrição de público "
        "ou só abrir com login na plataforma."
    )


class NoVideoInPostError(DownloadError):
    """Sinal específico do yt-dlp: o post/pin não tem vídeo nenhum (só imagem) -- TikTok,
    Instagram e Pinterest (03/10/2026, aprovação do CÉREBRO, depois de confirmar em produção que
    o yt-dlp recusa a extração nesse caso em vez de cair para a imagem sozinho: "No video formats
    found!" no Pinterest, "There is no video in this post" no Instagram).

    NUNCA chega ao usuário como está: download/service.py intercepta e tenta
    download/image_fallback.py antes de desistir. Só vira GENERIC_USER_MESSAGE (via
    DownloadFailedError) se o fallback de imagem também falhar."""

    user_message = GENERIC_USER_MESSAGE


class InvalidFileError(DownloadError):
    """O arquivo baixado não existe, está vazio, ou não é um vídeo válido."""

    user_message = GENERIC_USER_MESSAGE
