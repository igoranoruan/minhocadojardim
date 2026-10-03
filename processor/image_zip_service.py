"""Processamento de CARROSSEL do Instagram (vários slides no mesmo post, entregues como um único
.zip pela camada de download -- 03/10/2026, aprovação do CÉREBRO: "Todas as fotos, num .zip").

Equivalente a processor/image_service.py::process_image, mas para um .zip de várias imagens: abre
o .zip de entrada, remove o metadado de CADA imagem dentro dele (reaproveitando
processor.image_service.strip_image_metadata -- mesma lógica Pillow, sem duplicar), e grava um
NOVO .zip de saída só com as imagens já limpas. NÃO usa FFmpeg/ffprobe (mesma regra de
image_service.py) -- continuam exclusivamente de vídeo.

O tamanho do .zip de ENTRADA já foi conferido na camada de download
(download.file_validation.validate_downloaded_image_zip, contra MAX_IMAGE_ZIP_SIZE_BYTES) antes de
chegar aqui -- não repetimos essa checagem para o arquivo de entrada, só para o de SAÍDA (o
reencode/regravação do Pillow pode, em tese, produzir um arquivo de tamanho diferente do original).
"""
import hashlib
import logging
import zipfile

from pathlib import Path

from config import MAX_CAROUSEL_IMAGES, MAX_IMAGE_ZIP_SIZE_BYTES
from processor.errors import (
    InvalidInputFileError,
    InvalidOutputFileError,
    OutputTooLargeError,
    ProcessorError,
)
from processor.image_service import pil_format_for_extension, strip_image_metadata
from processor.result import ProcessingResult
from processor.tempfiles import cleanup, new_temp_stub

logger = logging.getLogger("minhoca")

_SHA256_CHUNK_SIZE = 1024 * 1024  # mesmo padrão de processor/image_service.py


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_SHA256_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def process_image_zip(input_path: Path) -> ProcessingResult:
    """Processa `input_path` (.zip de carrossel já baixado e validado pela Etapa 5) e devolve um
    novo .zip com cada imagem sem metadado. Levanta ProcessorError (ou subclasse) em qualquer
    falha; nesse caso, todo arquivo temporário de SAÍDA já criado (.zip final e os stubs de cada
    imagem individual) é removido antes de propagar o erro. O arquivo de ENTRADA nunca é apagado
    por esta camada (mesma regra de process_image/process_video)."""
    stub = new_temp_stub()
    output_path = stub.with_suffix(".zip")
    image_stubs: list[Path] = []

    try:
        try:
            with zipfile.ZipFile(input_path) as input_zip:
                names = [n for n in input_zip.namelist() if not n.endswith("/")]
        except zipfile.BadZipFile as exc:
            raise InvalidInputFileError(f".zip inválido: {exc.__class__.__name__}") from None

        if not names:
            raise InvalidInputFileError(".zip sem nenhuma imagem.")
        if len(names) > MAX_CAROUSEL_IMAGES:
            raise InvalidInputFileError(f"{len(names)} imagens no .zip > limite de {MAX_CAROUSEL_IMAGES}")

        try:
            with zipfile.ZipFile(input_path) as input_zip, zipfile.ZipFile(
                output_path, "w", compression=zipfile.ZIP_STORED
            ) as output_zip:
                for name in names:
                    extension = Path(name).suffix.lstrip(".").lower()
                    pil_format = pil_format_for_extension(extension)
                    if pil_format is None:
                        raise InvalidInputFileError(f"extensão de imagem não reconhecida no .zip: {extension!r}")

                    image_stub = new_temp_stub()
                    image_stubs.append(image_stub)
                    entry_input_path = image_stub.with_suffix(f".in.{extension}")
                    entry_output_path = image_stub.with_suffix(f".out.{extension}")

                    with input_zip.open(name) as source, entry_input_path.open("wb") as dest:
                        dest.write(source.read())

                    strip_image_metadata(entry_input_path, entry_output_path, pil_format)
                    output_zip.write(entry_output_path, arcname=name)
        except zipfile.BadZipFile as exc:
            raise InvalidInputFileError(f".zip inválido: {exc.__class__.__name__}") from None
        finally:
            # Os stubs de cada imagem individual (entrada/saída temporárias usadas só para passar
            # pelo Pillow) nunca fazem parte do resultado -- só o .zip final importa daqui pra
            # frente, sucesso ou erro.
            for image_stub in image_stubs:
                cleanup(image_stub)

        if not output_path.exists() or output_path.stat().st_size <= 0:
            raise InvalidOutputFileError("arquivo de saída vazio ou ausente.")

        output_size = output_path.stat().st_size
        if output_size > MAX_IMAGE_ZIP_SIZE_BYTES:
            raise OutputTooLargeError(f"{output_size} bytes > limite de {MAX_IMAGE_ZIP_SIZE_BYTES} bytes")

        with zipfile.ZipFile(output_path) as check_zip:
            bad_entry = check_zip.testzip()  # confere a integridade de cada entrada (defesa extra)
            if bad_entry is not None:
                raise InvalidOutputFileError(f"entrada corrompida no .zip de saída: {bad_entry!r}")

        output_sha256 = _sha256_of(output_path)  # só DEPOIS da validação final (nunca antes)
        logger.info(
            "[PROCESSOR] (carrossel) concluído entrada=%s saida=%s imagens=%s bytes=%s",
            input_path.name, output_path.name, len(names), output_size,
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
            "[PROCESSOR] (carrossel) falha inesperada tipo=%s", exc.__class__.__name__, exc_info=True,
        )
        raise ProcessorError() from None
