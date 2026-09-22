"""Identificação da plataforma a partir da URL. Só olha o host — nunca faz requisição de rede.

A lista de domínios é FECHADA: um host fora dela é UnsupportedPlatformError, nunca uma tentativa
"genérica". Isso também é a primeira camada de defesa contra SSRF (ver url_safety.py e
ytdlp_downloader.py): o yt-dlp só é chamado com o extractor específico da plataforma detectada
aqui, nunca com o extractor genérico dele.
"""
import enum
from urllib.parse import urlsplit

from download.errors import UnsupportedPlatformError


class Platform(enum.Enum):
    TIKTOK = "tiktok"
    INSTAGRAM = "instagram"
    PINTEREST = "pinterest"
    YOUTUBE = "youtube"


# Domínios oficiais de cada plataforma (com e sem "www.", e os encurtadores/variantes conhecidos).
_DOMAINS: dict[Platform, frozenset[str]] = {
    Platform.TIKTOK: frozenset({"tiktok.com", "www.tiktok.com", "vm.tiktok.com", "vt.tiktok.com", "m.tiktok.com"}),
    Platform.INSTAGRAM: frozenset({"instagram.com", "www.instagram.com"}),
    Platform.PINTEREST: frozenset({
        "pinterest.com", "www.pinterest.com", "pin.it",
        "pinterest.co.uk", "pinterest.ca", "pinterest.de", "pinterest.fr",
    }),
    Platform.YOUTUBE: frozenset({
        "youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be",
    }),
}

# host -> Platform (montado uma vez, na importação).
_HOST_TO_PLATFORM: dict[str, Platform] = {
    host: platform for platform, hosts in _DOMAINS.items() for host in hosts
}


def hosts_for(platform: Platform) -> frozenset[str]:
    return _DOMAINS[platform]


def detect_platform(url: str) -> Platform:
    """Identifica a plataforma pelo host da URL. Levanta UnsupportedPlatformError se não reconhecer.

    Chame isto DEPOIS de url_safety.validate_url(url) — esta função não valida segurança, só lê o
    host (já confiando que a URL passou pela validação de esquema/formato).
    """
    host = (urlsplit(url).hostname or "").lower()
    platform = _HOST_TO_PLATFORM.get(host)
    if platform is None:
        raise UnsupportedPlatformError(f"host não suportado: {host!r}" if host else "URL sem host")
    return platform
