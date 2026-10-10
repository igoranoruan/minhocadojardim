"""Listagem de pins de uma pasta/coleção do Pinterest (Etapa 3, 10/10/2026 -- aprovação do CÉREBRO).

Objetivo: dado o link de uma pasta pública do Pinterest, devolver a LISTA de pins (id, link do
pin, miniatura, se tem vídeo) -- NUNCA baixa vídeo nenhum aqui. É só leitura de metadados.

Reaproveita a mesma validação de segurança já usada por download/service.py (SSRF, allowlist de
domínio, allowlist de extractor) -- nada de novo nessa frente:
- download.url_safety.validate_url: mesma checagem de esquema/IP privado/DNS.
- download.platform.detect_platform: mesma allowlist fechada de domínios (só PINTEREST aqui).
- allowed_extractors=("PinterestCollection",): MESMO princípio do download de vídeo (nunca o
  extractor genérico) -- só que para o extractor de PASTA do yt-dlp, não o de pin único.

Nome do extractor ("PinterestCollection") confirmado por duas fontes cruzadas (10/10/2026): (1) a
regra de nomenclatura do próprio yt-dlp -- `InfoExtractor.IE_NAME` por padrão é o nome da classe
sem os 2 últimos caracteres ("PinterestCollectionIE"[:-2] == "PinterestCollection"); (2) essa MESMA
regra já explica, hoje, por que `allowed_extractors=("Pinterest",)` funciona em produção para
`PinterestIE` ("PinterestIE"[:-2] == "Pinterest"). Mesmo assim, PENDENTE DE CONFIRMAÇÃO EMPÍRICA
com uma pasta pública real (o CÉREBRO pediu isso explicitamente antes de fechar esta etapa) --
ver o comando de teste local que acompanha o relatório desta etapa.

SOBRE FILTRAR SÓ VÍDEO (pedido do CÉREBRO: a pasta tem foto E vídeo misturados, só queremos
vídeo): a leitura do código-fonte do extractor (`PinterestCollectionIE._real_extract`, 10/10/2026)
mostra que CADA pin da pasta passa por `_extract_video(item)` (o MESMO método que extrai um pin
individual) antes de entrar na lista -- ou seja, a pasta inteira já vem com `formats`/`duration`
preenchidos por pin, igual a uma extração individual. `extract_flat` não muda nada aqui pelo mesmo
motivo (o extractor de pasta já faz a extração completa por dentro -- ver download/ytdlp_downloader.py
para o uso real de extract_flat, que aqui não se aplica).

CONFIRMADO EMPIRICAMENTE (10/10/2026, pastas reais do Igor, via logs do Render em produção) --
e isso corrigiu uma suposição errada que eu tinha antes de ter acesso a uma pasta real: um pin SÓ
DE IMAGEM não entra silenciosamente com `formats: []`. O próprio yt-dlp (`YoutubeDL.process_video_result`,
em `YoutubeDL.py`) trata "sem nenhum formato de vídeo" como ERRO ("No video formats found!") por
padrão, porque o caso comum do yt-dlp é "isso deveria ser sempre um vídeo". Sem tratamento, isso
aborta a EXTRAÇÃO DA PASTA INTEIRA assim que bate no primeiro pin que é só foto -- foi exatamente
o que aconteceu nos testes reais do Igor (toda pasta com foto E vídeo misturados falhava).
A correção é a opção `ignore_no_formats_error: True` em `_build_options()` abaixo: com ela, o
yt-dlp rebaixa isso para um aviso (não gera exceção) e devolve o pin normalmente com
`formats: []`/`duration: None` -- exatamente o que `_has_video` abaixo precisa para marcar o pin
como "sem vídeo" em vez de derrubar a pasta toda. Confirmado lendo `raise_no_formats` em
`extractor/common.py` e `YoutubeDL.raise_no_formats` em `YoutubeDL.py` do código-fonte real do
yt-dlp (mesmo clone usado para confirmar o nome do extractor).
"""
import logging
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from urllib.parse import urlsplit

import yt_dlp

from config import BOARD_LIST_MAX_ITEMS, BOARD_LIST_TIMEOUT_SECONDS
from download.errors import DownloadFailedError, DownloadTimeoutError, UnsupportedPlatformError
from download.platform import Platform, detect_platform
from download.url_safety import resolve_redirect_chain, validate_url

logger = logging.getLogger("minhoca")

# Mesmo papel de download/service.py::_SHORT_LINK_HOSTS, só que restrito ao Pinterest: um link
# pin.it de pasta também precisa ser resolvido para a URL longa antes do yt-dlp (mesma causa raiz
# documentada em download/service.py -- o extractor de pasta também não reconhece o encurtador).
_SHORT_LINK_HOSTS = frozenset({"pin.it"})


class BoardNotFoundError(DownloadFailedError):
    """Pasta inexistente, privada, ou sem nenhum pin -- mesma mensagem genérica (nunca revela
    qual dos três motivos foi, mesma filosofia do 404 genérico já usado em outras rotas)."""

    user_message = "Não foi possível acessar esta pasta do Pinterest agora."


@dataclass(frozen=True)
class BoardPin:
    pin_id: str
    pin_url: str
    thumbnail_url: str | None
    has_video: bool


@dataclass(frozen=True)
class BoardListing:
    pins: list[BoardPin]
    has_more: bool  # True se a pasta tinha mais pins do que BOARD_LIST_MAX_ITEMS


def _build_options() -> dict:
    return {
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": False,  # o OPOSTO do download de vídeo -- aqui QUEREMOS a pasta inteira
        "playlist_items": f"1-{BOARD_LIST_MAX_ITEMS + 1}",  # +1 só para detectar has_more
        "allowed_extractors": ["PinterestCollection"],
        "nocheckcertificate": False,
        "socket_timeout": BOARD_LIST_TIMEOUT_SECONDS,
        "cookiefile": None,
        # Confirmado em produção (10/10/2026, pastas reais): sem isso, um pin só de imagem faz o
        # yt-dlp levantar "No video formats found!" e aborta a listagem DA PASTA INTEIRA no
        # primeiro pin sem vídeo, em vez de devolver esse pin com formats=[] (ver docstring do
        # módulo). Com True, vira só um aviso interno do yt-dlp -- nunca aparece para o usuário.
        "ignore_no_formats_error": True,
        # Confirmado em produção (10/10/2026, pasta real de 71 pins do Igor): existe pelo menos
        # um TIPO de pin que nem `ignore_no_formats_error` acima cobre -- um pin cujo conteúdo é
        # um GIF/arquivo externo embutido (`domain` != "uploaded by user" + `embed.src`, dentro de
        # `_extract_video` no código-fonte do yt-dlp). Esse tipo faz o yt-dlp tentar resolver a URL
        # do arquivo embutido com um extractor GENÉRICO -- e como `allowed_extractors` acima é
        # restrito a só "PinterestCollection" (de propósito, nunca o extractor genérico, mesmo
        # princípio de download/service.py), isso falha com "No suitable extractor found for URL
        # ...", e SEM essa opção aqui também abortava a listagem da PASTA INTEIRA, não só aquele
        # pin. Com `ignoreerrors: True`, só ESSE pin problemático é descartado da lista (não conta
        # cota, não aparece na grade) -- os outros pins da pasta continuam normalmente. Reproduzido
        # e confirmado com o yt-dlp real (mesmo clone usado nas confirmações anteriores): sem a
        # opção, o erro exato de produção se repete; com ela, só o pin problemático some da lista.
        "ignoreerrors": True,
    }


def _best_thumbnail(entry: dict) -> str | None:
    thumbnails = entry.get("thumbnails") or []
    if not thumbnails:
        return entry.get("thumbnail")
    return thumbnails[-1].get("url")  # yt-dlp ordena do menor para o maior; o último é o melhor


def _has_video(entry: dict) -> bool:
    """Ver docstring do módulo: `formats` não-vazio é o sinal de que o pin tem vídeo."""
    return bool(entry.get("formats"))


def _run_extract(url: str, options: dict) -> dict:
    with yt_dlp.YoutubeDL(options) as ydl:
        return ydl.extract_info(url, download=False)


def _extract_with_timeout(url: str, options: dict) -> dict:
    """Mesmo padrão (e mesmo motivo) de download/service.py::_download_with_timeout: o executor é
    criado/encerrado à mão (nunca `with ThreadPoolExecutor() as executor:`), porque o __exit__ do
    context manager chama shutdown(wait=True) e anularia o próprio watchdog no caminho de timeout."""
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(_run_extract, url, options)
    try:
        result = future.result(timeout=BOARD_LIST_TIMEOUT_SECONDS)
    except FutureTimeoutError:
        executor.shutdown(wait=False)
        raise DownloadTimeoutError("listagem de pasta do Pinterest excedeu o tempo limite") from None
    else:
        executor.shutdown(wait=True)  # a tarefa já terminou: isto não bloqueia de fato
        return result


def list_board_pins(url: str) -> BoardListing:
    """Valida a URL (mesma proteção de SSRF/allowlist de domínio de sempre), confirma que é
    Pinterest, resolve encurtador se precisar, e lista os pins da pasta via yt-dlp -- SEM baixar
    nenhum vídeo. Levanta UnsupportedPlatformError (reaproveitado de download/errors.py) se a URL
    não for do Pinterest, ou BoardNotFoundError/DownloadTimeoutError em caso de falha."""
    validated = validate_url(url)
    platform = detect_platform(validated.url)
    if platform is not Platform.PINTEREST:
        raise UnsupportedPlatformError("listagem de pasta só é suportada para Pinterest")

    host = (urlsplit(validated.url).hostname or "").lower()
    if host in _SHORT_LINK_HOSTS:
        validated = resolve_redirect_chain(validated.url)

    options = _build_options()
    try:
        info = _extract_with_timeout(validated.url, options)
    except DownloadTimeoutError:
        raise
    except yt_dlp.utils.DownloadError as exc:
        logger.warning("[PINTEREST_BOARD] falha ao listar pasta: %s", exc.__class__.__name__)
        raise BoardNotFoundError() from None
    except Exception:
        logger.error("[PINTEREST_BOARD] falha inesperada ao listar pasta", exc_info=True)
        raise BoardNotFoundError() from None

    entries = [e for e in (info.get("entries") or []) if isinstance(e, dict)]
    if not entries:
        raise BoardNotFoundError()

    has_more = len(entries) > BOARD_LIST_MAX_ITEMS
    entries = entries[:BOARD_LIST_MAX_ITEMS]

    pins = [
        BoardPin(
            pin_id=str(entry.get("id")),
            pin_url=entry.get("webpage_url") or validated.url,
            thumbnail_url=_best_thumbnail(entry),
            has_video=_has_video(entry),
        )
        for entry in entries
        if entry.get("id") is not None
    ]
    return BoardListing(pins=pins, has_more=has_more)
