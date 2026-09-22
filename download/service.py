"""Ponto de entrada único da camada de download.

Fluxo: URL -> validação (url_safety) -> identificação da plataforma (platform) -> downloader
(ytdlp_downloader) -> arquivo temporário -> validação do arquivo (file_validation) -> resultado
padronizado (result.DownloadResult). Nada aqui conhece planos, gerações, pagamentos, sessão,
banco ou frontend — é consumido pela Etapa 6, nunca o contrário.

LIMITAÇÃO DE TIMEOUT (documentada): Python não tem uma forma segura de interromper uma thread no
meio de uma chamada bloqueante. O watchdog abaixo (ThreadPoolExecutor + future.result(timeout=...))
garante que o CHAMADOR nunca espera além de DOWNLOAD_TIMEOUT_SECONDS — isso foi CONFIRMADO por
teste (o executor é encerrado com shutdown(wait=False) no caminho de timeout; usar
`with ThreadPoolExecutor() as executor:` aqui seria um bug sutil, porque o __exit__ do context
manager bloqueia em shutdown(wait=True) e anularia o próprio watchdog — por isso o executor é
gerenciado manualmente). O que continua fora do nosso controle é a chamada ao yt-dlp em si, que
pode continuar rodando em segundo plano até terminar sozinha (mitigado por `socket_timeout`, que
evita travas indefinidas por um socket parado). Isso é aceitável para o escopo desta etapa:
nenhuma linha de `generations` é criada aqui (isso é Etapa 4, feito por quem chamar
`download_video`), então uma chamada "esquecida" em segundo plano não consome cota nem deixa o
produto em estado inconsistente — só ocupa uma thread até o próprio `socket_timeout` interno
encerrar a tentativa.
"""
import logging
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError

from config import DOWNLOAD_TIMEOUT_SECONDS, MAX_VIDEO_DURATION_SECONDS
from download.base import PlatformDownloader, RawDownload
from download.errors import DownloadError, DownloadFailedError, DownloadTimeoutError, VideoTooLongError
from download.file_validation import validate_downloaded_file
from download.platform import Platform, detect_platform
from download.result import DownloadResult
from download.tempfiles import cleanup, new_temp_stub
from download.url_safety import validate_url
from download.ytdlp_downloader import YtDlpDownloader, YtDlpSpec

logger = logging.getLogger("minhoca")

_SPECS: dict[Platform, YtDlpSpec] = {
    Platform.TIKTOK: YtDlpSpec(Platform.TIKTOK, allowed_extractors=("TikTok",)),
    Platform.INSTAGRAM: YtDlpSpec(Platform.INSTAGRAM, allowed_extractors=("Instagram",)),
    Platform.PINTEREST: YtDlpSpec(Platform.PINTEREST, allowed_extractors=("Pinterest",)),
    Platform.YOUTUBE: YtDlpSpec(
        Platform.YOUTUBE,
        allowed_extractors=("Youtube",),
        extra_opts={
            # mweb + PO Token Provider (BGUTIL), quando o companion estiver disponível no
            # ambiente. PO Token NÃO garante todos os vídeos (especificação do produto): sem o
            # companion, alguns vídeos simplesmente falham com DownloadFailedError diagnosticável
            # no log, em vez de tentar dezenas de fallbacks (regra "sem fallback em cascata").
            "extractor_args": {"youtube": {"player_client": ["mweb"]}},
        },
    ),
}

_DOWNLOADERS: dict[Platform, PlatformDownloader] = {
    platform: YtDlpDownloader(spec) for platform, spec in _SPECS.items()
}


def download_video(url: str) -> DownloadResult:
    """Baixa e valida um vídeo. Levanta DownloadError (ou subclasse) em qualquer falha; nesse
    caso, todo arquivo temporário já criado é removido antes de propagar o erro."""
    validated = validate_url(url)
    platform = detect_platform(validated.url)
    downloader = _DOWNLOADERS[platform]
    stub = new_temp_stub()

    try:
        raw = _download_with_timeout(downloader, validated.url, stub)
        _check_duration(raw)
        size = validate_downloaded_file(raw.path)
        return DownloadResult(
            platform=platform,
            temp_path=raw.path,
            size_bytes=size,
            duration_seconds=raw.duration_seconds,
            container_format=raw.path.suffix.lstrip("."),
        )
    except DownloadError:
        cleanup(stub)
        raise
    except Exception as exc:
        cleanup(stub)
        logger.error(
            "[DOWNLOAD] falha inesperada plataforma=%s tipo=%s", platform.value, exc.__class__.__name__,
            exc_info=True,
        )
        raise DownloadFailedError() from None


def _check_duration(raw: RawDownload) -> None:
    if raw.duration_seconds is not None and raw.duration_seconds > MAX_VIDEO_DURATION_SECONDS:
        raise VideoTooLongError(f"{raw.duration_seconds}s > {MAX_VIDEO_DURATION_SECONDS}s (informado pela plataforma)")


def _download_with_timeout(downloader: PlatformDownloader, url: str, stub) -> RawDownload:
    # NÃO usar `with ThreadPoolExecutor() as executor:` aqui: o __exit__ do context manager chama
    # shutdown(wait=True), que bloqueia até a thread terminar — ou seja, mesmo depois do timeout
    # abaixo, o chamador ficaria esperando o download lento terminar de qualquer jeito, anulando
    # o watchdog. Por isso o executor é criado e encerrado à mão, com wait=False no caminho de
    # timeout (só nesse caminho o chamador realmente precisa não esperar).
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(downloader.download, url, stub)
    try:
        result = future.result(timeout=DOWNLOAD_TIMEOUT_SECONDS)
    except FutureTimeoutError:
        executor.shutdown(wait=False)
        raise DownloadTimeoutError() from None
    else:
        executor.shutdown(wait=True)  # a tarefa já terminou: isto não bloqueia de fato
        return result
