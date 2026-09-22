"""Erros padronizados da camada de processamento.

Toda falha desta camada é uma instância de ProcessorError (ou subclasse). Cada uma carrega uma
mensagem GENÉRICA (`user_message`), segura para mostrar ao usuário, e o detalhe técnico vai só
para o log de quem a levanta (mesma regra de download/errors.py). O stderr bruto do FFmpeg NUNCA
vira a mensagem do usuário — só entra, sanitizado/truncado, no `detail` (log-only).
"""

GENERIC_USER_MESSAGE = "Não foi possível processar este vídeo agora."


class ProcessorError(Exception):
    """Base de todos os erros da camada de processamento."""

    user_message = GENERIC_USER_MESSAGE

    def __init__(self, detail: str | None = None) -> None:
        self.detail = detail or self.user_message
        super().__init__(self.detail)


class InvalidInputFileError(ProcessorError):
    """Arquivo de entrada inexistente, vazio, ou que o ffprobe não reconhece como mídia válida."""

    user_message = GENERIC_USER_MESSAGE


class InputTooLargeError(ProcessorError):
    user_message = "O vídeo é maior do que o permitido."


class InputTooLongError(ProcessorError):
    user_message = "O vídeo é mais longo do que o permitido."


class NoVideoStreamError(ProcessorError):
    """O arquivo não tem nenhum stream de vídeo (ex.: só áudio)."""

    user_message = "O arquivo enviado não contém vídeo."


class FfprobeUnavailableError(ProcessorError):
    """O binário do ffprobe não foi encontrado no ambiente."""

    user_message = GENERIC_USER_MESSAGE


class FfprobeInvalidOutputError(ProcessorError):
    """O ffprobe rodou, mas devolveu algo que não é o JSON esperado."""

    user_message = GENERIC_USER_MESSAGE


class FfmpegUnavailableError(ProcessorError):
    """O binário do FFmpeg não foi encontrado no ambiente, ou não tem o encoder exigido (libx264)."""

    user_message = GENERIC_USER_MESSAGE


class FfmpegFailedError(ProcessorError):
    """O processo do FFmpeg terminou com erro (código de saída diferente de zero)."""

    user_message = GENERIC_USER_MESSAGE


class ProcessingTimeoutError(ProcessorError):
    user_message = "O processamento demorou demais e foi cancelado."


class InvalidOutputFileError(ProcessorError):
    """O MP4 gerado não existe, está vazio, ou o ffprobe não o reconhece como válido."""

    user_message = GENERIC_USER_MESSAGE


class OutputTooLargeError(ProcessorError):
    user_message = "O resultado do processamento ficou maior do que o permitido."
