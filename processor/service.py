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

from collections.abc import Callable
from pathlib import Path

from processor.errors import ProcessingTimeoutError, ProcessorError
from processor.ffmpeg import run_ffmpeg
from processor.probe import ProbeResult
from processor.result import ProcessingResult
from processor.tempfiles import cleanup, new_temp_stub
from processor.validation import validate_input, validate_output

logger = logging.getLogger("minhoca")

_SHA256_CHUNK_SIZE = 1024 * 1024  # lê o arquivo final em pedaços para o hash — nunca tudo em RAM

# Otimização de performance (caminho rápido / stream copy): três modos possíveis, na ordem de
# preferência abaixo -- copiar nunca muda nada perceptível (resolução/FPS/qualidade/duração
# idênticas para o que é copiado), só evita o trabalho de reencodar.
#   "copy"                       -- vídeo E áudio (se houver) já nos codecs exatos que o
#                                    transcode produziria de qualquer forma: nada é reencodado.
#   "copy_video_transcode_audio" -- só o VÍDEO já está no codec exato (H.264); o áudio (se houver)
#                                    não está (ex.: Opus, comum em streams do YouTube) -- copia o
#                                    vídeo (grátis) e reencoda só o áudio (barato comparado a
#                                    reencodar vídeo em libx264, otimização de performance, 30/09,
#                                    aprovação do CÉREBRO).
#   "transcode"                  -- vídeo não está no codec exato (VP9, AV1, HEVC, etc.):
#                                    reencoda os dois, como sempre.
# Qualquer combinação fora das duas primeiras cai em "transcode" -- nunca assumimos que "deve dar
# certo": só copiamos o que temos certeza de que o MP4 final aceita sem conversão.
_COPY_SAFE_VIDEO_CODECS = frozenset({"h264"})
_COPY_SAFE_AUDIO_CODECS = frozenset({"aac"})


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_SHA256_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _escolher_modo_rapido(probe_result: ProbeResult) -> str:
    """Decide qual dos três modos usar PRIMEIRO (antes de qualquer tentativa/fallback real de
    FFmpeg) -- ver o comentário de _COPY_SAFE_VIDEO_CODECS/_COPY_SAFE_AUDIO_CODECS acima para a
    regra completa de quando cada um é seguro.

    "copy": vídeo H.264 e (sem áudio OU áudio já AAC) -- nada precisa ser reencodado.
    "copy_video_transcode_audio": vídeo H.264, mas há áudio e ele não é AAC -- só o áudio precisa
    ser reencodado (otimização de performance, 30/09, aprovação do CÉREBRO).
    "transcode": vídeo não é H.264 -- reencoda os dois, como sempre (nunca assumimos "deve dar
    certo": qualquer combinação fora das duas primeiras cai aqui)."""
    if probe_result.video_codec not in _COPY_SAFE_VIDEO_CODECS:
        return "transcode"
    if probe_result.has_audio and probe_result.audio_codec not in _COPY_SAFE_AUDIO_CODECS:
        return "copy_video_transcode_audio"
    return "copy"


def process_video(
    input_path: Path, *, on_progress: Callable[[float | None], None] | None = None,
) -> ProcessingResult:
    """Processa `input_path` (arquivo já baixado, ex.: DownloadResult.temp_path da Etapa 5) e
    devolve um MP4 validado. Levanta ProcessorError (ou subclasse) em qualquer falha; nesse caso,
    todo arquivo temporário de SAÍDA já criado é removido antes de propagar o erro. O arquivo de
    ENTRADA nunca é apagado por esta camada — quem o criou (Etapa 5) decide sobre ele.

    `on_progress` (opcional, barra de progresso real): repassado direto a run_ffmpeg, junto com
    `input_probe.duration_seconds` -- a duração REAL do vídeo de entrada, que este módulo já
    calculava antes desta etapa (validate_input já chama o probe), só não era reaproveitada.

    Otimização de performance (caminho rápido / stream copy, 3 modos -- ver
    `_escolher_modo_rapido`): quando o probe confirma que o vídeo de entrada já é H.264, tenta
    primeiro `run_ffmpeg(mode="copy")` (vídeo E áudio copiados, se o áudio já for AAC) ou
    `run_ffmpeg(mode="copy_video_transcode_audio")` (só o vídeo copiado, áudio reencodado, quando o
    áudio não é AAC -- otimização de performance, 30/09, aprovação do CÉREBRO: ainda MUITO mais
    rápido que reencodar vídeo em libx264). Se a tentativa escolhida falhar com um erro RÁPIDO do
    FFmpeg (ex.: FfmpegFailedError -- arquivo real incompatível apesar do probe), cai
    automaticamente para o caminho de transcodificação completa de sempre (`mode="transcode"`, o
    único usado antes desta otimização) -- nenhuma tentativa rápida pode, sozinha, fazer uma
    geração falhar.

    Exceção deliberada (vale para as DUAS tentativas rápidas, "copy" e
    "copy_video_transcode_audio"): um ProcessingTimeoutError NÃO tenta "transcode" em seguida --
    mesmo copiando o vídeo, se o FFmpeg travar por 180s isso é sinal de um problema no próprio
    arquivo, não de a estratégia rápida ser a errada; tentar transcodificar depois só dobraria o
    tempo de espera até muito provavelmente travar de novo. Timeout continua propagando
    imediatamente, exatamente como antes desta etapa (nenhuma mudança em PROCESSING_TIMEOUT_SECONDS)."""
    input_probe = validate_input(input_path)  # InvalidInputFileError/InputTooLargeError/... propagam
    stub = new_temp_stub()
    output_path = stub.with_suffix(".mp4")

    modo = _escolher_modo_rapido(input_probe)
    try:
        if modo != "transcode":
            try:
                run_ffmpeg(
                    input_path=input_path, output_path=output_path, has_audio=input_probe.has_audio,
                    duration_seconds=input_probe.duration_seconds, on_progress=on_progress, mode=modo,
                )
            except ProcessingTimeoutError:
                raise
            except ProcessorError:
                logger.warning(
                    "[PROCESSOR] caminho rápido (%s) falhou para %s -- caindo para "
                    "transcodificação completa", modo, input_path.name, exc_info=True,
                )
                cleanup(stub)
                modo = "transcode"

        if modo == "transcode":
            run_ffmpeg(
                input_path=input_path, output_path=output_path, has_audio=input_probe.has_audio,
                duration_seconds=input_probe.duration_seconds, on_progress=on_progress, mode="transcode",
            )

        output_probe = validate_output(output_path)
        output_size = output_path.stat().st_size
        output_sha256 = _sha256_of(output_path)  # só DEPOIS da validação final (nunca antes)
        logger.info(
            "[PROCESSOR] concluído entrada=%s saida=%s bytes=%s duracao=%ss audio=%s modo=%s",
            input_path.name, output_path.name, output_size, output_probe.duration_seconds,
            output_probe.has_audio, modo,
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
