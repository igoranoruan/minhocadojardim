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

Estratégia (duas camadas, 03/10/2026 -- correção de corte/crop depois de teste real em produção
confirmar que a tag og:image do Instagram devolve uma miniatura RECORTADA, nunca a imagem original
inteira):
1. PRIMEIRO tenta ler o "display_url" de dentro do JSON que o Instagram já embute na própria página
   (a mesma informação que o navegador usa para renderizar a imagem em tela cheia) -- é a imagem
   ORIGINAL, sem corte. Ancorado pelo "shortcode" da URL (extraído do próprio link, ex.: .../p/
   ABC123/ -> "ABC123") para nunca pegar a imagem de um post sugerido/relacionado que também apareça
   na mesma página -- só o trecho do JSON que pertence a ESTE post. Específico do Instagram (as
   outras duas plataformas não têm esse formato de página); se o shortcode ou o JSON não forem
   encontrados (página mudou, ou não é Instagram), cai para a estratégia 2 sem erro.
2. Tags <meta property="og:image"> / <meta name="twitter:image"> -- o mesmo dado que qualquer
   pré-visualização de link (WhatsApp, Slack, Twitter/X, etc.) já lê dessas páginas. Funciona nas
   três plataformas; no Instagram é só o fallback de ÚLTIMO recurso (pode vir cortado), no TikTok/
   Pinterest é a estratégia principal (nenhum problema de corte observado nelas).

Em nenhum dos dois casos é scraping autenticado nem contorna login: é o mesmo dado que a própria
plataforma já publica para qualquer visitante anônimo. Usa curl_cffi com impersonation de navegador
(MESMO mecanismo e MESMA dependência já usados pelo TikTok em download/ytdlp_downloader.py) porque
pelo menos o TikTok já exige esse fingerprint para servir a própria página (ver docstring do
YtDlpSpec do TikTok).

CARROSSEL DO INSTAGRAM (vários slides no mesmo post, 03/10/2026 -- aprovação do CÉREBRO, depois de
perguntar explicitamente como entregar): quando `_instagram_display_urls` encontra MAIS de uma
imagem, `fetch_post_image` baixa TODAS (até MAX_CAROUSEL_IMAGES, proteção contra post com
quantidade anormal de slides -- ver config.py) e devolve um ÚNICO .zip com todas elas, reaproveitando
o pipeline de UM arquivo de resultado por geração (download/service.py decide o media_type pela
extensão real do arquivo, igual a vídeo/imagem única -- "zip" é só mais uma extensão reconhecida,
ver download/file_validation.py). Post com UMA imagem só continua devolvendo essa imagem direto,
sem nenhuma mudança de comportamento (mesma extensão, mesmo caminho de código de antes desta
mudança).

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
import zipfile
from pathlib import Path

from curl_cffi import requests as curl_requests

from config import (
    DOWNLOAD_CONNECT_TIMEOUT_SECONDS,
    MAX_CAROUSEL_IMAGES,
    MAX_IMAGE_SIZE_BYTES,
    MAX_IMAGE_ZIP_SIZE_BYTES,
    MAX_REDIRECTS,
)
from download.base import RawDownload
from download.errors import (
    DownloadFailedError,
    ImageTooLargeError,
    ImageZipTooLargeError,
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

# Instagram: shortcode da URL (.../p/<shortcode>/, .../reel/<shortcode>/, .../tv/<shortcode>/) --
# usado só para ANCORAR a busca no JSON embutido na página (nunca pegar a imagem de outro post).
_INSTAGRAM_SHORTCODE_PATTERN = re.compile(r"/(?:p|reel|reels|tv)/([A-Za-z0-9_-]+)")
_SHORTCODE_JSON_KEY = re.compile(r'"shortcode"\s*:\s*"')
_DISPLAY_URL_PATTERN = re.compile(r'"display_url"\s*:\s*"([^"]+)"')


def _unescape_json_string_url(value: str) -> str:
    """Desfaz o escaping de string JSON de uma URL (\\/ -> /, \\u0026 -> &, etc.) sem precisar de
    um parser JSON completo -- só os pontos que realmente aparecem numa URL."""
    return (
        value.replace("\\/", "/")
        .replace("\\u0026", "&")
        .replace("\\u003d", "=")
        .replace("&amp;", "&")
    )


def _instagram_display_urls(html: str, post_url: str) -> list[str]:
    """Imagens ORIGINAIS (sem corte) do post, na ordem -- 1 para post simples, várias para
    carrossel. Ancorado pelo shortcode da própria URL para nunca pegar o JSON de um post
    sugerido/relacionado que apareça na mesma página. Lista vazia (nunca erro) se o shortcode ou o
    JSON esperado não forem encontrados -- quem chama cai para o fallback de og:image nesse caso."""
    shortcode_match = _INSTAGRAM_SHORTCODE_PATTERN.search(post_url)
    if not shortcode_match:
        return []
    shortcode = shortcode_match.group(1)
    anchor = re.search(r'"shortcode"\s*:\s*"%s"' % re.escape(shortcode), html)
    if not anchor:
        return []
    # Janela: do shortcode encontrado até o PRÓXIMO "shortcode" na página (início do JSON do
    # próximo post, ex.: sugestões) -- ou o fim da página, se não houver outro.
    rest = html[anchor.end():]
    next_shortcode = _SHORTCODE_JSON_KEY.search(rest)
    window = rest[: next_shortcode.start()] if next_shortcode else rest
    urls = [_unescape_json_string_url(u) for u in _DISPLAY_URL_PATTERN.findall(window)]
    # dedup preservando ordem (o mesmo display_url às vezes aparece mais de uma vez no JSON).
    seen: set[str] = set()
    ordered: list[str] = []
    for url in urls:
        if url not in seen:
            seen.add(url)
            ordered.append(url)
    return ordered


def _extract_image_urls(html: str, post_url: str) -> list[str]:
    """Todas as imagens candidatas do post, na ordem de preferência: JSON embutido do Instagram
    (original, sem corte; pode ter mais de uma, ver _instagram_display_urls) primeiro, depois
    og:image/twitter:image (as três plataformas; único caminho para TikTok/Pinterest)."""
    instagram_urls = _instagram_display_urls(html, post_url)
    if instagram_urls:
        return instagram_urls
    for pattern in _META_IMAGE_PATTERNS:
        match = pattern.search(html)
        if match:
            return [_unescape_json_string_url(match.group(1))]
    return []


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


def _download_single_image(image_url: str) -> tuple[bytes, str]:
    """Baixa UMA imagem (já com redirecionamentos validados) e devolve (bytes, extensão). Levanta
    DownloadError (ou subclasse) em qualquer falha -- usado tanto pelo caminho de imagem única
    quanto, uma vez por slide, pelo caminho de carrossel/.zip abaixo."""
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

    return content, extension


def fetch_post_image(url: str, dest_stub: Path) -> RawDownload:
    """Busca a(s) imagem(ns) publicada(s) na página pública de `url` (post/pin sem vídeo) e grava
    em `dest_stub` + extensão. Post com uma imagem só: grava a imagem direto (extensão decidida
    pelo Content-Type da resposta, comportamento IDÊNTICO ao de antes do suporte a carrossel).
    Carrossel do Instagram (mais de uma imagem): baixa todas (até MAX_CAROUSEL_IMAGES) e grava um
    único `dest_stub.zip` com todas elas. Levanta DownloadError (ou subclasse) em qualquer falha --
    mesmo contrato de PlatformDownloader.download, embora esta função não implemente essa interface
    (é chamada diretamente por download/service.py, só para o caminho de fallback)."""
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

    image_urls = _extract_image_urls(page.text, url)
    if not image_urls:
        logger.warning("[DOWNLOAD] fallback de imagem: nenhuma imagem encontrada na página do post")
        raise DownloadFailedError()

    if len(image_urls) == 1:
        content, extension = _download_single_image(image_urls[0])
        output_path = dest_stub.with_suffix(f".{extension}")
        output_path.write_bytes(content)
        return RawDownload(path=output_path, duration_seconds=None)

    # Carrossel: várias imagens no mesmo post -- baixa todas (limitadas a MAX_CAROUSEL_IMAGES, só
    # proteção contra uma quantidade anormal de slides; o Instagram permite até 10 na prática) e
    # empacota num único .zip (decisão de produto do CÉREBRO, 03/10/2026).
    bounded_urls = image_urls[:MAX_CAROUSEL_IMAGES]
    output_path = dest_stub.with_suffix(".zip")
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_STORED) as zip_file:
        for index, image_url in enumerate(bounded_urls, start=1):
            content, extension = _download_single_image(image_url)
            zip_file.writestr(f"foto_{index:02d}.{extension}", content)

    zip_size = output_path.stat().st_size
    if zip_size > MAX_IMAGE_ZIP_SIZE_BYTES:
        output_path.unlink(missing_ok=True)
        raise ImageZipTooLargeError(f"{zip_size} bytes > limite de {MAX_IMAGE_ZIP_SIZE_BYTES} bytes")

    return RawDownload(path=output_path, duration_seconds=None)
