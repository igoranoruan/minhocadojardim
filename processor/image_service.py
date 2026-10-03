"""Processamento de IMAGEM (Pinterest/Instagram sem vídeo, 03/10/2026, aprovação do CÉREBRO).

Equivalente a processor/service.py::process_video, mas para imagem — NÃO usa FFmpeg nem ffprobe
(as duas ferramentas do processor/ continuam exclusivamente de vídeo, propositalmente intocadas
nesta mudança). Usa Pillow (já disponível no ambiente) só para abrir e regravar o arquivo SEM
nenhum metadado: Pillow só grava EXIF/ICC quando esses parâmetros são passados explicitamente a
`Image.save` — como este módulo nunca os passa, regravar já é suficiente para "limpar metadados"
de uma imagem (não há reencode de qualidade/resolução equivalente ao vídeo, nem precisa haver).

O tamanho já foi conferido na camada de download (download.file_validation.validate_downloaded_image,
contra MAX_IMAGE_SIZE_BYTES) antes do arquivo chegar aqui — não repetimos essa checagem.
"""
import hashlib
import logging

from pathlib import Path

from PIL import Image, UnidentifiedImageError

from processor.errors import InvalidInputFileError, InvalidOutputFileError, ProcessorError
from processor.result import ProcessingResult
from processor.tempfiles import cleanup, new_temp_stub

logger = logging.getLogger("minhoca")

_SHA256_CHUNK_SIZE = 1024 * 1024  # mesmo padrão de processor/service.py: nunca o arquivo inteiro em RAM

# Extensão -> formato que o Pillow espera em Image.save(format=...). Mantido em sincronia com
# download.file_validation.ALLOWED_IMAGE_EXTENSIONS (quem decide a extensão real é a Etapa 5).
_PIL_FORMAT_FOR_EXTENSION = {
    "jpg": "JPEG",
    "jpeg": "JPEG",
    "png": "PNG",
    "webp": "WEBP",
}


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_SHA256_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def pil_format_for_extension(extension: str) -> str | None:
    """"JPEG"/"PNG"/"WEBP" para uma extensão de imagem reconhecida, ou None. Reaproveitado por
    processor/image_zip_service.py (carrossel do Instagram, 03/10/2026) para validar cada imagem
    dentro do .zip com a MESMA lista usada aqui, sem duplicar o dicionário."""
    return _PIL_FORMAT_FOR_EXTENSION.get(extension.lower())


def strip_image_metadata(input_path: Path, output_path: Path, pil_format: str) -> None:
    """Abre `input_path` com Pillow e regrava em `output_path` SEM metadado (ver docstring do
    módulo) no formato `pil_format`. Levanta InvalidInputFileError/InvalidOutputFileError -- mesmo
    contrato usado por `process_image` abaixo e por processor/image_zip_service.py (carrossel), que
    reaproveita esta função uma vez por imagem do carrossel em vez de duplicar a lógica."""
    try:
        with Image.open(input_path) as image:
            image.load()  # força a leitura completa agora -- um arquivo truncado falha aqui
            if pil_format == "JPEG" and image.mode not in ("RGB", "L"):
                image = image.convert("RGB")  # JPEG não suporta modos com canal alfa (ex.: RGBA)
            image.save(output_path, format=pil_format)
    except (UnidentifiedImageError, OSError) as exc:
        raise InvalidInputFileError(f"imagem inválida: {exc.__class__.__name__}") from None

    if not output_path.exists() or output_path.stat().st_size <= 0:
        raise InvalidOutputFileError("arquivo de saída vazio ou ausente.")

    with Image.open(output_path) as check_image:
        check_image.verify()  # confere que a imagem regravada ainda é válida (defesa extra)


def process_image(input_path: Path) -> ProcessingResult:
    """Processa `input_path` (imagem já baixada e validada pela Etapa 5, Pinterest/Instagram sem
    vídeo) e devolve uma cópia sem metadados. Levanta ProcessorError (ou subclasse) em qualquer
    falha; nesse caso, todo arquivo temporário de SAÍDA já criado é removido antes de propagar o
    erro. O arquivo de ENTRADA nunca é apagado por esta camada (mesma regra de process_video)."""
    extension = input_path.suffix.lstrip(".").lower()
    pil_format = _PIL_FORMAT_FOR_EXTENSION.get(extension)
    if pil_format is None:
        raise InvalidInputFileError(f"extensão de imagem não reconhecida: {extension!r}")

    stub = new_temp_stub()
    output_path = stub.with_suffix(f".{extension}")

    try:
        strip_image_metadata(input_path, output_path, pil_format)

        output_size = output_path.stat().st_size
        output_sha256 = _sha256_of(output_path)  # só DEPOIS da validação final (nunca antes)
        logger.info(
            "[PROCESSOR] (imagem) concluído entrada=%s saida=%s bytes=%s",
            input_path.name, output_path.name, output_size,
        )
        return ProcessingResult(
            output_path=output_path,
            size_bytes=output_size,
            duration_seconds=0.0,
            has_audio=False,
            output_sha256=output_sha256,
        )
    except ProcessorError:
        cleanup(stub)
        raise
    except Exception as exc:
        cleanup(stub)
        logger.error(
            "[PROCESSOR] (imagem) falha inesperada tipo=%s", exc.__class__.__name__, exc_info=True,
        )
        raise ProcessorError() from None
