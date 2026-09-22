"""Ponto de entrada único da camada de processamento.

Fluxo: arquivo de entrada -> probe + validação de entrada -> FFmpeg -> validação de saída ->
SHA-256 -> ProcessingResult. Nada aqui conhece rotas, planos, gerações, pagamentos, banco ou
frontend — é consumido por uma etapa futura (que também vai chamar download.service.download_video
antes desta função), nunca o contrário.

Processamento é SEQUENCIAL (um FFmpeg por vez): não há nenhum mecanismo de paralelismo aqui. Se
chamado várias vezes ao mesmo tempo por quem o usa, cada chamada roda seu próprio processo FFmpeg
de forma independente — a serialização "1 por vez", se necessária, é decisão de quem orquestra o
uso desta camada (fora do escopo desta etapa: sem fila, sem lock global aqui).
"""
import hashlib
import logging

from pathlib import Path

from processor.errors import ProcessorError
from processor.ffmpeg import run_ffmpeg
from processor.result import ProcessingResult
from processor.tempfiles import cleanup, new_temp_stub
from processor.validation import validate_input, validate_output

logger = logging.getLogger("minhoca")

_SHA256_CHUNK_SIZE = 1024 * 1024  # lê o arquivo final em pedaços para o hash — nunca tudo em RAM


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_SHA256_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def process_video(input_path: Path) -> ProcessingResult:
    """Processa `input_path` (arquivo já baixado, ex.: DownloadResult.temp_path da Etapa 5) e
    devolve um MP4 validado. Levanta ProcessorError (ou subclasse) em qualquer falha; nesse caso,
    todo arquivo temporário de SAÍDA já criado é removido antes de propagar o erro. O arquivo de
    ENTRADA nunca é apagado por esta camada — quem o criou (Etapa 5) decide sobre ele."""
    input_probe = validate_input(input_path)  # InvalidInputFileError/InputTooLargeError/... propagam
    stub = new_temp_stub()
    output_path = stub.with_suffix(".mp4")

    try:
        run_ffmpeg(input_path=input_path, output_path=output_path, has_audio=input_probe.has_audio)
        output_probe = validate_output(output_path)
        output_size = output_path.stat().st_size
        output_sha256 = _sha256_of(output_path)  # só DEPOIS da validação final (nunca antes)
        logger.info(
            "[PROCESSOR] concluído entrada=%s saida=%s bytes=%s duracao=%ss audio=%s",
            input_path.name, output_path.name, output_size, output_probe.duration_seconds,
            output_probe.has_audio,
        )
        return ProcessingResult(
            output_path=output_path,
            size_bytes=output_size,
            duration_seconds=output_probe.duration_seconds or 0.0,
            has_audio=output_probe.has_audio,
            output_sha256=output_sha256,
        )
    except ProcessorError:
        cleanup(stub)
        raise
    except Exception as exc:
        cleanup(stub)
        logger.error("[PROCESSOR] falha inesperada tipo=%s", exc.__class__.__name__, exc_info=True)
        raise ProcessorError() from None
