"""Fallback de IMAGEM para post/pin SEM vídeo no TikTok, Instagram e Pinterest (03/10/2026,
aprovação do CÉREBRO, depois de confirmar em produção que o yt-dlp RECUSA a extração nesse caso em
vez de cair para a imagem sozinho -- a suposição original era errada: "No video formats found!" no
Pinterest, "There is no video in this post" no Instagram; ver download/errors.py::NoVideoInPostError
e download/ytdlp_downloader.py). Os extractors de vídeo dessas plataformas são feitos só pra vídeo;
não existe opção do yt-dlp para "baixar a imagem quando não houver vídeo".

Acionado SÓ por download/service.py, e SÓ quando o downloader de vídeo já respondeu com
NoVideoInPostError -- nunca para qualquer outro motivo de falha (vídeo privado, removido, timeout,
rate-limit etc. continuam EXATAMENTE como estavam, sem nenhuma tentativa extra, sem nenhuma mudança
de comportamento no caminho de vídeo).

Estratégia: ler a própria página pública do post/pin e extrair a imagem das tags
<meta property="og:image"> / <meta name="twitter:image"> -- o mesmo dado que qualquer
pré-visualização de link (WhatsApp, Slack, Twitter/X, etc.) já lê dessas páginas: não é scraping
autenticado, não contorna login nenhum, e não lê nada que a própria plataforma não publique para
qualquer visitante anônimo. Usa curl_cffi com impersonation de navegador (MESMO mecanismo e MESMA
dependência já usados pelo TikTok em download/ytdlp_downloader.py) porque pelo menos o TikTok já
exige esse fingerprint para servir a própria página (ver docstring do YtDlpSpec do TikTok).

SSRF: reaproveita a MESMA validação de download/url_safety.py (validate_url) -- a URL de entrada já
foi validada e teve encurtadores resolvidos por download/service.py antes de chegar aqui; os
redirecionamentos desta camada (página do post e, depois, a própria imagem) são seguidos
MANUALMENTE, revalidando cada salto, nunca automático -- mesma postura do resto do módulo (ver
limitação documentada em url_safety.py sobre o que isso NÃO cobre).

Tamanho: carrega o corpo da resposta inteiro em memória antes de checar MAX_IMAGE_SIZE_BYTES (ao
contrário do vídeo, que nunca faz isso -- ver ytdlp_downloader.py). Aceitável aqui porque o limite
de imagem é pequeno (25 MB, ver config.py), bem abaixo do limite de vídeo (100 MB) que motivou a
escolha de streaming por lá.
"""
import logging
import re
from pathlib import Path

from curl_cffi import requests as curl_requests

from config import DOWNLOAD_CONNECT_TIMEOUT_SECONDS, MAX_IMAGE_SIZE_BYTES, MAX_REDIRECTS
from download.base import RawDownload
from download.errors import (
    DownloadFailedError,
    ImageTooLargeError,
    InvalidUrlError,
    SsrfBlockedError,
    TooManyRedirectsError,
)
from download.url_safety import validate_url

logger = logging.getLogger("minhoca")

# Impersonar um navegador real -- sem isso, pelo menos o TikTok já bloqueia a própria página (ver
# YtDlpSpec do TikTok em download/ytdlp_downloader.py). Mesmo pacote (curl_cffi) já usado lá.
_IMPERSONATE = "chrome"

# og:image (com a variante :secure_url, usada por algumas páginas) e twitter:image, aceitando
# aspas simples ou duplas e qualquer ordem entre os atributos property/name e content.
_META_IMAGE_PATTERNS = (
    re.compile(
        r'<meta[^>]+property=["\']og:image(?::secure_url)?["\'][^>]+content=["\']([^"\']+)["\']',
        re.IGNORECASE,
    ),
    re.compile(
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image(?::secure_url)?["\']',
        re.IGNORECASE,
    ),
    re.compile(
        r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
        re.IGNORECASE,
    ),
    re.compile(
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image["\']',
        re.IGNORECASE,
    ),
)

_CONTENT_TYPE_TO_EXTENSION = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}


def _extract_image_url(html: str) -> str | None:
    for pattern in _META_IMAGE_PATTERNS:
        match = pattern.search(html)
        if match:
            return match.group(1).replace("&amp;", "&")
    return None


def _get_with_validated_redirects(url: str, *, max_redirects: int = MAX_REDIRECTS):
    """GET seguindo redirecionamentos MANUALMENTE, revalidando CADA salto com
    url_safety.validate_url ANTES de seguir -- nunca automático (mesma postura de
    url_safety.resolve_redirect_chain, adaptada aqui para GET via curl_cffi em vez de HEAD via
    urllib, porque pelo menos o TikTok exige impersonation de navegador para responder)."""
    current = validate_url(url)
    seen = {current.url}
    session = curl_requests.Session()
    for _ in range(max_redirects):
        response = session.get(
            current.url,
            impersonate=_IMPERSONATE,
            timeout=DOWNLOAD_CONNECT_TIMEOUT_SECONDS,
            allow_redirects=False,
        )
        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("Location")
            if not location:
                return response
            next_validated = validate_url(location)
            if next_validated.url in seen:
                raise TooManyRedirectsError("redirecionamento cíclico detectado.")
            seen.add(next_validated.url)
            current = next_validated
            continue
        return response
    raise TooManyRedirectsError(f"mais de {max_redirects} redirecionamentos.")


def fetch_post_image(url: str, dest_stub: Path) -> RawDownload:
    """Busca a imagem publicada na página pública de `url` (post/pin sem vídeo) e grava em
    `dest_stub` + extensão (decidida pelo Content-Type da resposta). Levanta DownloadError (ou
    subclasse) em qualquer falha -- mesmo contrato de PlatformDownloader.download, embora esta
    função não implemente essa interface (é chamada diretamente por download/service.py, só para
    o caminho de fallback)."""
    try:
        page = _get_with_validated_redirects(url)
    except (InvalidUrlError, SsrfBlockedError, TooManyRedirectsError):
        raise
    except Exception as exc:
        logger.warning(
            "[DOWNLOAD] fallback de imagem: falha ao buscar a página do post (%s)",
            exc.__class__.__name__,
        )
        raise DownloadFailedError() from None

    if page.status_code != 200:
        logger.warning("[DOWNLOAD] fallback de imagem: página do post respondeu status=%s", page.status_code)
        raise DownloadFailedError()

    image_url = _extract_image_url(page.text)
    if not image_url:
        logger.warning("[DOWNLOAD] fallback de imagem: nenhuma tag og:image/twitter:image encontrada")
        raise DownloadFailedError()

    try:
        image_response = _get_with_validated_redirects(image_url)
    except (InvalidUrlError, SsrfBlockedError, TooManyRedirectsError):
        raise
    except Exception as exc:
        logger.warning(
            "[DOWNLOAD] fallback de imagem: falha ao baixar a imagem (%s)", exc.__class__.__name__,
        )
        raise DownloadFailedError() from None

    if image_response.status_code != 200:
        logger.warning(
            "[DOWNLOAD] fallback de imagem: download da imagem respondeu status=%s", image_response.status_code,
        )
        raise DownloadFailedError()

    content = image_response.content
    if not content:
        raise DownloadFailedError("imagem vazia.")
    if len(content) > MAX_IMAGE_SIZE_BYTES:
        raise ImageTooLargeError(f"{len(content)} bytes > limite de {MAX_IMAGE_SIZE_BYTES} bytes")

    content_type = (image_response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    extension = _CONTENT_TYPE_TO_EXTENSION.get(content_type)
    if extension is None:
        # Sem Content-Type reconhecido: última tentativa pela extensão da própria URL da imagem.
        suffix = Path(image_url.split("?")[0]).suffix.lstrip(".").lower()
        extension = suffix if suffix in _CONTENT_TYPE_TO_EXTENSION.values() else None
    if extension is None:
        logger.warning("[DOWNLOAD] fallback de imagem: tipo de imagem não reconhecido (content-type=%r)", content_type)
        raise DownloadFailedError("tipo de imagem não reconhecido.")

    output_path = dest_stub.with_suffix(f".{extension}")
    output_path.write_bytes(content)
    return RawDownload(path=output_path, duration_seconds=None)
