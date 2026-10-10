"""Listagem de pasta/coleção do Pinterest (Etapa 3, 10/10/2026 -- aprovação do CÉREBRO).

Mocks do yt-dlp -- nenhuma chamada real de rede (mesmo padrão de
tests/test_download_ytdlp_downloader.py). NÃO testa a orquestração de plano/rate-limit (isso é
tests/test_pinterest_board_service.py) -- só a extração/parsing em si.
"""
import socket
from unittest.mock import patch

import pytest
import yt_dlp

from download.board_listing import (
    BoardNotFoundError,
    _best_thumbnail,
    _has_video,
    list_board_pins,
)
from download.errors import DownloadTimeoutError, UnsupportedPlatformError


@pytest.fixture(autouse=True)
def _dns_publico(monkeypatch):
    """Nenhuma chamada real de DNS/rede nestes testes -- mesmo padrão de
    tests/test_download_url_safety.py: qualquer host "resolve" para um IP público fixo."""
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, port: [(2, 1, 6, "", ("93.184.216.34", 0))])


def _entry(pin_id="123", webpage_url=None, formats=None, thumbnails=None):
    """`formats`/`thumbnails` usam `is None` (nunca `or`) -- um `[]` EXPLÍCITO precisa permanecer
    vazio (ex.: simular um pin sem miniatura), não virar o valor padrão por engano."""
    return {
        "id": pin_id,
        "webpage_url": webpage_url or f"https://www.pinterest.com/pin/{pin_id}/",
        "formats": formats if formats is not None else [],
        "thumbnails": (
            thumbnails
            if thumbnails is not None
            else [{"url": "https://exemplo/pequena.jpg"}, {"url": "https://exemplo/grande.jpg"}]
        ),
    }


# ------------------------------------------------------------------ _has_video / _best_thumbnail
def test_has_video_true_quando_formats_nao_vazio():
    assert _has_video(_entry(formats=[{"url": "https://exemplo/video.mp4"}])) is True


def test_has_video_false_quando_formats_vazio():
    assert _has_video(_entry(formats=[])) is False


def test_best_thumbnail_pega_a_ultima_da_lista():
    entrada = _entry(thumbnails=[{"url": "pequena"}, {"url": "grande"}])
    assert _best_thumbnail(entrada) == "grande"


def test_best_thumbnail_usa_fallback_sem_lista():
    entrada = _entry(thumbnails=[])
    entrada["thumbnail"] = "unica.jpg"
    assert _best_thumbnail(entrada) == "unica.jpg"


# ------------------------------------------------------------------ list_board_pins
def test_url_que_nao_e_pinterest_e_rejeitada():
    with pytest.raises(UnsupportedPlatformError):
        list_board_pins("https://www.tiktok.com/@alguem/pasta")


def test_lista_pins_misturando_foto_e_video_com_flag_correta():
    info = {
        "entries": [
            _entry(pin_id="1", formats=[{"url": "v.mp4"}]),  # tem vídeo
            _entry(pin_id="2", formats=[]),  # só foto
        ]
    }
    with patch("download.board_listing._extract_with_timeout", return_value=info):
        listing = list_board_pins("https://www.pinterest.com/alguem/pasta/")

    assert [p.pin_id for p in listing.pins] == ["1", "2"]
    assert listing.pins[0].has_video is True
    assert listing.pins[1].has_video is False
    assert listing.has_more is False


def test_has_more_quando_pasta_excede_o_limite():
    from config import BOARD_LIST_MAX_ITEMS

    entries = [_entry(pin_id=str(i)) for i in range(BOARD_LIST_MAX_ITEMS + 1)]
    with patch("download.board_listing._extract_with_timeout", return_value={"entries": entries}):
        listing = list_board_pins("https://www.pinterest.com/alguem/pasta/")

    assert len(listing.pins) == BOARD_LIST_MAX_ITEMS
    assert listing.has_more is True


def test_pasta_sem_pins_vira_board_not_found_error():
    with patch("download.board_listing._extract_with_timeout", return_value={"entries": []}):
        with pytest.raises(BoardNotFoundError):
            list_board_pins("https://www.pinterest.com/alguem/pasta-vazia/")


def test_falha_do_yt_dlp_vira_board_not_found_error():
    with patch(
        "download.board_listing._extract_with_timeout",
        side_effect=yt_dlp.utils.DownloadError("pasta privada ou inexistente"),
    ):
        with pytest.raises(BoardNotFoundError):
            list_board_pins("https://www.pinterest.com/alguem/pasta-privada/")


def test_timeout_propaga_como_download_timeout_error():
    with patch("download.board_listing._extract_with_timeout", side_effect=DownloadTimeoutError()):
        with pytest.raises(DownloadTimeoutError):
            list_board_pins("https://www.pinterest.com/alguem/pasta/")


def test_opcoes_nunca_usam_extractor_generico():
    from download.board_listing import _build_options

    options = _build_options()
    assert options["allowed_extractors"] == ["PinterestCollection"]
    assert "generic" not in [e.lower() for e in options["allowed_extractors"]]
    assert options["nocheckcertificate"] is False
    assert options["cookiefile"] is None
    assert options["skip_download"] is True
    # Confirmado em produção (10/10/2026): sem isso, um pin só de imagem derruba a extração da
    # pasta INTEIRA ("No video formats found!") em vez de simplesmente entrar com formats=[].
    assert options["ignore_no_formats_error"] is True
    # Confirmado em produção (10/10/2026, pasta real de 71 pins): sem isso, um pin tipo GIF/arquivo
    # externo embutido também derruba a pasta INTEIRA ("No suitable extractor found for URL ...").
    # Com True, só aquele pin específico é descartado -- os outros continuam na lista normalmente.
    assert options["ignoreerrors"] is True


def test_resolve_link_curto_pin_it_antes_de_chamar_o_yt_dlp():
    """pin.it (mesmo encurtador já tratado em download/service.py) precisa ser resolvido para a
    URL longa ANTES do yt-dlp -- mesma causa raiz já documentada para o download de pin único."""
    from download.url_safety import ValidatedUrl

    resolvida = ValidatedUrl(
        url="https://www.pinterest.com/alguem/pasta/",
        scheme="https",
        host="www.pinterest.com",
        port=443,
        resolved_ips=("1.2.3.4",),
    )
    with patch("download.board_listing.resolve_redirect_chain", return_value=resolvida) as mock_resolve:
        with patch("download.board_listing._extract_with_timeout", return_value={"entries": [_entry()]}) as mock_extract:
            list_board_pins("https://pin.it/abc123")

    mock_resolve.assert_called_once()
    # o yt-dlp é chamado com a URL JÁ resolvida (longa), nunca com o link curto original.
    assert mock_extract.call_args[0][0] == "https://www.pinterest.com/alguem/pasta/"
