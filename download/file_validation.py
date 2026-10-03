"""Validação básica do arquivo baixado: existe, não está vazio, extensão permitida e dentro do
tamanho máximo. NÃO valida duração aqui (isso é ffprobe, que só entra oficialmente na Etapa 6 —
ver DownloadResult.duration_seconds em download/result.py).

03/10/2026 (suporte a imagem -- Pinterest/Instagram também servem pin/post SEM vídeo, só imagem,
aprovação do CÉREBRO): a validação de VÍDEO (ALLOWED_EXTENSIONS/validate_downloaded_file) continua
EXATAMENTE como estava antes desta mudança -- mesma assinatura, mesmo comportamento, mesmos testes
já existentes. O suporte a imagem ganhou seu PRÓPRIO conjunto de extensões e sua PRÓPRIA função de
validação (ALLOWED_IMAGE_EXTENSIONS/validate_downloaded_image), lado a lado, nunca misturados --
quem decide QUAL validar é download/service.py, a partir da extensão REAL do arquivo baixado
(detect_media_type, abaixo), nunca do que a URL/plataforma dizia que era.
"""
from pathlib import Path

from config import MAX_IMAGE_SIZE_BYTES, MAX_IMAGE_ZIP_SIZE_BYTES, MAX_VIDEO_SIZE_BYTES
from download.errors import ImageTooLargeError, ImageZipTooLargeError, InvalidFileError, VideoTooLargeError

# Extensões que o yt-dlp pode produzir para vídeo (com merge_output_format=mp4, o caso comum é
# sempre .mp4; os outros ficam como rede de segurança para conteúdo que não passa por merge).
ALLOWED_EXTENSIONS = frozenset({"mp4", "mkv", "webm", "mov", "m4v"})

# Extensões de imagem que o yt-dlp já entrega quando um pin do Pinterest ou um post do Instagram
# não tem vídeo nenhum -- o próprio extractor cai numa imagem-fallback nesse caso, e o format
# "best" já usado por essas duas plataformas (download/service.py) escolhe essa imagem sem
# precisar de NENHUMA mudança no seletor de format (só vídeo tem seletor dedicado nesta camada).
ALLOWED_IMAGE_EXTENSIONS = frozenset({"jpg", "jpeg", "png", "webp"})

# Carrossel do Instagram (vários slides no mesmo post, 03/10/2026 -- aprovação do CÉREBRO):
# download/image_fallback.py entrega um .zip com todas as imagens quando o post tem mais de uma.
# Extensão PRÓPRIA (nunca misturada com ALLOWED_IMAGE_EXTENSIONS): um .zip não é uma imagem --
# processor/image_service.py (Pillow) não abre um .zip, por isso precisa de media_type diferente
# ("image_zip") para download/service.py e services/generation_flow.py escolherem a validação e o
# processamento certos.
ALLOWED_IMAGE_ZIP_EXTENSIONS = frozenset({"zip"})


def detect_media_type(path: Path) -> str | None:
    """"video" | "image" | "image_zip" | None (extensão não reconhecida em nenhuma das três
    listas), a partir da extensão REAL do arquivo baixado. Usado por download/service.py para
    decidir qual das validações abaixo chamar -- não valida nada sozinho (nem existência, nem
    tamanho)."""
    extension = path.suffix.lstrip(".").lower()
    if extension in ALLOWED_EXTENSIONS:
        return "video"
    if extension in ALLOWED_IMAGE_EXTENSIONS:
        return "image"
    if extension in ALLOWED_IMAGE_ZIP_EXTENSIONS:
        return "image_zip"
    return None


def _file_size(path: Path) -> int:
    if not path.exists() or not path.is_file():
        raise InvalidFileError(f"arquivo não encontrado: {path.name}")
    size = path.stat().st_size
    if size <= 0:
        raise InvalidFileError("arquivo vazio.")
    return size


def validate_downloaded_file(path: Path) -> int:
    """Confere existência, extensão e tamanho de um VÍDEO. Devolve o tamanho em bytes.

    Levanta InvalidFileError/VideoTooLargeError e NÃO apaga o arquivo — a limpeza é sempre
    responsabilidade de quem orquestra (download/service.py chama download/tempfiles.cleanup).
    """
    extension = path.suffix.lstrip(".").lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise InvalidFileError(f"extensão não permitida: {extension!r}")

    size = _file_size(path)
    if size > MAX_VIDEO_SIZE_BYTES:
        raise VideoTooLargeError(f"{size} bytes > limite de {MAX_VIDEO_SIZE_BYTES} bytes")
    return size


def validate_downloaded_image(path: Path) -> int:
    """Confere existência, extensão e tamanho de uma IMAGEM (pin/post do Pinterest/Instagram sem
    vídeo). Mesmo contrato de validate_downloaded_file (devolve o tamanho em bytes, nunca apaga o
    arquivo), só que com a lista de extensões e o limite de tamanho próprios de imagem -- NÃO
    confere se o conteúdo é mesmo uma imagem válida (isso é processor/image_service.py, via
    Pillow); aqui é só extensão e tamanho, igual ao vídeo nesta mesma camada."""
    extension = path.suffix.lstrip(".").lower()
    if extension not in ALLOWED_IMAGE_EXTENSIONS:
        raise InvalidFileError(f"extensão não permitida: {extension!r}")

    size = _file_size(path)
    if size > MAX_IMAGE_SIZE_BYTES:
        raise ImageTooLargeError(f"{size} bytes > limite de {MAX_IMAGE_SIZE_BYTES} bytes")
    return size


def validate_downloaded_image_zip(path: Path) -> int:
    """Confere existência, extensão e tamanho do .zip de um CARROSSEL do Instagram (vários slides
    no mesmo post, 03/10/2026 -- aprovação do CÉREBRO). Mesmo contrato das duas funções acima
    (devolve o tamanho em bytes, nunca apaga o arquivo), com a extensão e o limite de tamanho
    próprios do .zip -- NÃO confere o conteúdo do .zip (quantidade de imagens, se cada uma é uma
    imagem válida, etc.) -- isso é processor/image_zip_service.py, igual ao padrão já usado para
    vídeo/imagem única nesta mesma camada."""
    extension = path.suffix.lstrip(".").lower()
    if extension not in ALLOWED_IMAGE_ZIP_EXTENSIONS:
        raise InvalidFileError(f"extensão não permitida: {extension!r}")

    size = _file_size(path)
    if size > MAX_IMAGE_ZIP_SIZE_BYTES:
        raise ImageZipTooLargeError(f"{size} bytes > limite de {MAX_IMAGE_ZIP_SIZE_BYTES} bytes")
    return size
