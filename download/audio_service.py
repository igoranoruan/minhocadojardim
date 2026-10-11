"""Download da FONTE de áudio (Parte A/B, autorização do CÉREBRO, 11/10/2026).

Pede ao yt-dlp só a faixa de ÁUDIO ("bestaudio/best") sempre que a plataforma oferecer um stream
de áudio separado (YouTube e Pinterest, confirmado em download/service.py -- os dois servem
vídeo/áudio em streams DASH/HLS separados) -- isso evita baixar o vídeo inteiro só para descartar a
imagem depois, economizando tempo/memória no Render (mesmo objetivo das otimizações de performance
já documentadas em download/service.py). Quando NÃO há stream de áudio separado (TikTok/Instagram
tipicamente só servem formatos já muxados), "bestaudio/best" cai automaticamente para "best" --
baixa o vídeo completo, exatamente como o fluxo de vídeo já faz -- e quem chama este módulo
(processor/audio_service.py) extrai a faixa de áudio dele via FFmpeg. Esta "queda automática" é o
próprio yt-dlp resolvendo o format selector, não uma lógica nossa de fallback.

Reaproveita INTEGRALMENTE a validação de URL/SSRF, a detecção de plataforma, a resolução de link
curto e o watchdog de timeout já existentes em download/service.py e download/url_safety.py -- nada
disso é duplicado. NÃO reaproveita o fallback de imagem (download/image_fallback.py): um post sem
vídeo nem áudio separado não tem "imagem com áudio" nenhuma para tentar -- vira NoAudioInPostError
direto.

A validação de arquivo (extensão/tamanho) é PRÓPRIA deste módulo, não a de
download/file_validation.py: aquela só reconhece extensões de VÍDEO (mp4/mkv/webm/mov/m4v) --
"bestaudio" pode legitimamente devolver um contêiner só-áudio (m4a/webm/opus/ogg/mp3/aac/wav), que
file_validation.detect_media_type rejeitaria como "extensão não reconhecida".
"""
import logging
from pathlib import Path
from urllib.parse import urlsplit

from config import MAX_VIDEO_DURATION_SECONDS, MAX_VIDEO_SIZE_BYTES
from download.base import RawDownload
from download.errors import DownloadError, DownloadFailedError, InvalidFileError, NoVideoInPostError, VideoTooLargeError, VideoTooLongError
from download.platform import Platform, detect_platform
from download.result import DownloadResult
from download.service import _SHORT_LINK_HOSTS, _download_with_timeout
from download.tempfiles import cleanup, new_temp_stub
from download.url_safety import resolve_redirect_chain, validate_url
from download.ytdlp_downloader import YtDlpDownloader, YtDlpSpec

logger = logging.getLogger("minhoca")

# "bestaudio/best": tenta primeiro o melhor stream SÓ de áudio; sem um disponível, cai para "best"
# (o melhor format completo, vídeo+áudio) -- NUNCA falha por falta de format só porque não existe
# uma faixa de áudio separada (mesmo espírito do pedido do CÉREBRO: "se não houver áudio separado,
# cair no fluxo normal").
_AUDIO_FORMAT = "bestaudio/best"

# Mesma allowed_extractors de download/service.py por plataforma (NUNCA o extractor genérico --
# ver a análise de segurança em download/ytdlp_downloader.py/download/url_safety.py, reaproveitada
# sem nenhuma mudança aqui). "impersonate": "chrome" do TikTok é preservado (exigido para resolver
# o desafio anti-bot da plataforma, independente do format pedido).
_AUDIO_SPECS: dict[Platform, YtDlpSpec] = {
    Platform.TIKTOK: YtDlpSpec(
        Platform.TIKTOK, allowed_extractors=("TikTok",),
        extra_opts={"impersonate": "chrome", "format": _AUDIO_FORMAT},
    ),
    Platform.INSTAGRAM: YtDlpSpec(
        Platform.INSTAGRAM, allowed_extractors=("Instagram",), extra_opts={"format": _AUDIO_FORMAT},
    ),
    Platform.PINTEREST: YtDlpSpec(
        Platform.PINTEREST, allowed_extractors=("Pinterest",), extra_opts={"format": _AUDIO_FORMAT},
    ),
    Platform.YOUTUBE: YtDlpSpec(
        Platform.YOUTUBE, allowed_extractors=("Youtube",), extra_opts={"format": _AUDIO_FORMAT},
    ),
}
_AUDIO_DOWNLOADERS: dict[Platform, YtDlpDownloader] = {
    platform: YtDlpDownloader(spec) for platform, spec in _AUDIO_SPECS.items()
}

# Contêineres que "bestaudio/best" pode legitimamente produzir: só-áudio (bestaudio) OU vídeo
# completo (fallback "best", mesmas extensões de download/file_validation.ALLOWED_EXTENSIONS).
_ALLOWED_SOURCE_EXTENSIONS = frozenset(
    {"mp4", "mkv", "webm", "mov", "m4v", "m4a", "opus", "ogg", "aac", "mp3", "wav"}
)


class NoAudioInPostError(DownloadError):
    """O post/pin não tem vídeo NEM áudio (mesmo sinal que download/service.py trata como
    NoVideoInPostError para o fluxo de vídeo) -- aqui não existe fallback de imagem (áudio de uma
    imagem não faz sentido), então vira direto a mensagem pedida pelo CÉREBRO."""

    user_message = "Esse vídeo não tem áudio."


def _validate_downloaded_source(path: Path) -> int:
    """Confere existência, extensão (allow-list própria, acima) e tamanho da FONTE baixada --
    mesmo contrato de download/file_validation.validate_downloaded_file (devolve o tamanho em
    bytes, nunca apaga o arquivo), reaproveitando MAX_VIDEO_SIZE_BYTES como teto (mesma fonte já
    baixada, nunca um limite maior)."""
    extension = path.suffix.lstrip(".").lower()
    if extension not in _ALLOWED_SOURCE_EXTENSIONS:
        raise InvalidFileError(f"extensão não permitida: {extension!r}")
    if not path.exists() or not path.is_file():
        raise InvalidFileError(f"arquivo não encontrado: {path.name}")
    size = path.stat().st_size
    if size <= 0:
        raise InvalidFileError("arquivo vazio.")
    if size > MAX_VIDEO_SIZE_BYTES:
        raise VideoTooLargeError(f"{size} bytes > limite de {MAX_VIDEO_SIZE_BYTES} bytes")
    return size


def _check_duration(raw: RawDownload) -> None:
    if raw.duration_seconds is not None and raw.duration_seconds > MAX_VIDEO_DURATION_SECONDS:
        raise VideoTooLongError(f"{raw.duration_seconds}s > {MAX_VIDEO_DURATION_SECONDS}s (informado pela plataforma)")


def download_audio_source(url: str) -> DownloadResult:
    """Baixa a FONTE de áudio (stream só-áudio, ou o vídeo completo quando não há um stream
    separado) e valida o arquivo. Levanta DownloadError (ou subclasse) em qualquer falha; nesse
    caso, todo arquivo temporário já criado é removido antes de propagar o erro -- mesmo contrato
    de download.service.download_video."""
    validated = validate_url(url)
    platform = detect_platform(validated.url)

    # Mesma resolução de link curto (pin.it, vm/vt.tiktok.com) que download/service.py já faz --
    # reaproveitada por import, nunca duplicada.
    host = (urlsplit(validated.url).hostname or "").lower()
    if host in _SHORT_LINK_HOSTS.get(platform, frozenset()):
        validated = resolve_redirect_chain(validated.url)

    downloader = _AUDIO_DOWNLOADERS[platform]
    stub = new_temp_stub()

    try:
        try:
            raw = _download_with_timeout(lambda: downloader.download(validated.url, stub))
        except NoVideoInPostError:
            # Post sem vídeo (só imagem, ver download/service.py) também não tem áudio nenhum --
            # nunca tentamos o fallback de imagem aqui (não existe "áudio de uma imagem").
            raise NoAudioInPostError() from None
        _check_duration(raw)
        size = _validate_downloaded_source(raw.path)
        return DownloadResult(
            platform=platform,
            temp_path=raw.path,
            size_bytes=size,
            duration_seconds=raw.duration_seconds,
            container_format=raw.path.suffix.lstrip("."),
            media_type="audio",
        )
    except DownloadError:
        cleanup(stub)
        raise
    except Exception as exc:
        cleanup(stub)
        logger.error(
            "[AUDIO_DOWNLOAD] falha inesperada plataforma=%s tipo=%s", platform.value, exc.__class__.__name__,
            exc_info=True,
        )
        raise DownloadFailedError() from None
