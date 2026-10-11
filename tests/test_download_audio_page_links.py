"""download/audio_page_links.py: detecção de links de página de ÁUDIO (TikTok `/music/...`,
Instagram `/reels/audio/...`/`/audio/...`) -- Parte 1, autorização do CÉREBRO (11/10/2026).

Função pura (sem banco, sem rede) -- não precisa de nenhuma fixture de banco (diferente da
maioria dos testes deste projeto)."""
from download.audio_page_links import (
    AUDIO_PAGE_LINK_MESSAGE,
    AudioPageLinkError,
    is_audio_page_link,
    partition_audio_batch_items,
)
from download.errors import DownloadError


# ============================================================================ TikTok /music/
def test_tiktok_music_e_detectado():
    assert is_audio_page_link("https://www.tiktok.com/music/Build-a-Btch-6956990112127585029") is True
    assert is_audio_page_link("https://tiktok.com/music/jiefei-soap-remix-7036843036118469381") is True
    assert is_audio_page_link("https://m.tiktok.com/music/algo-123") is True
    # query string não muda o resultado
    assert is_audio_page_link("https://www.tiktok.com/music/Build-a-Btch-6956990112127585029?lang=en") is True


def test_video_normal_do_tiktok_nao_e_detectado():
    assert is_audio_page_link("https://www.tiktok.com/@usuario/video/123456") is False


def test_link_encurtado_do_tiktok_nao_e_detectado():
    """Limitação documentada no módulo: um encurtador que REDIRECIONA para uma página de áudio não
    é pego aqui (exigiria resolver o redirecionamento, fora do escopo desta checagem só-string)."""
    assert is_audio_page_link("https://vm.tiktok.com/ZMabc123/") is False
    assert is_audio_page_link("https://vt.tiktok.com/ZMabc123/") is False


# ============================================================================ Instagram /reels/audio/ e /audio/
def test_instagram_reels_audio_e_detectado():
    assert is_audio_page_link("https://www.instagram.com/reels/audio/123456789/") is True
    assert is_audio_page_link("https://instagram.com/reels/audio/123456789") is True


def test_instagram_audio_e_detectado():
    assert is_audio_page_link("https://www.instagram.com/audio/123456789/") is True


def test_reel_ou_post_normal_do_instagram_nao_e_detectado():
    assert is_audio_page_link("https://www.instagram.com/reel/Cabc123xyz/") is False
    assert is_audio_page_link("https://www.instagram.com/p/Cabc123xyz/") is False


# ============================================================================ outras plataformas / entrada inválida
def test_outras_plataformas_e_entrada_invalida_nunca_sao_detectadas():
    assert is_audio_page_link("https://www.pinterest.com/pin/123/") is False
    assert is_audio_page_link("https://www.youtube.com/watch?v=abc") is False
    assert is_audio_page_link("") is False
    assert is_audio_page_link(None) is False
    assert is_audio_page_link("nao-e-url") is False
    assert is_audio_page_link("   ") is False


# ============================================================================ AudioPageLinkError
def test_audio_page_link_error_e_download_error_com_mensagem_exata():
    assert issubclass(AudioPageLinkError, DownloadError)
    erro = AudioPageLinkError()
    assert erro.user_message == AUDIO_PAGE_LINK_MESSAGE
    assert erro.detail == AUDIO_PAGE_LINK_MESSAGE
    assert erro.user_message == (
        "Esse é o link da página do áudio. Abra um dos vídeos que usam esse som, "
        "toque em Compartilhar → Copiar link e cole aqui."
    )


# ============================================================================ partition_audio_batch_items
def test_partition_separa_pulados_dos_reais_preservando_posicao_original():
    items = [
        {"url": "https://www.tiktok.com/@a/video/1", "filename": None},
        {"url": "https://www.tiktok.com/music/som-123", "filename": None},
        {"url": "https://www.tiktok.com/@a/video/2", "filename": "b"},
        {"url": "https://www.instagram.com/reels/audio/999/", "filename": None},
        {"url": "https://www.tiktok.com/@a/video/3", "filename": None},
    ]
    pulados, posicoes_originais, itens_reais = partition_audio_batch_items(items)

    assert [posicao for posicao, _ in pulados] == [2, 4]
    assert posicoes_originais == [1, 3, 5]
    assert [item["url"] for item in itens_reais] == [
        "https://www.tiktok.com/@a/video/1",
        "https://www.tiktok.com/@a/video/2",
        "https://www.tiktok.com/@a/video/3",
    ]


def test_partition_lote_inteiro_de_links_de_audio():
    items = [{"url": "https://www.tiktok.com/music/x-1", "filename": None}]
    pulados, posicoes_originais, itens_reais = partition_audio_batch_items(items)
    assert len(pulados) == 1
    assert posicoes_originais == []
    assert itens_reais == []


def test_partition_sem_nenhum_link_de_audio():
    items = [{"url": "https://www.tiktok.com/@a/video/1", "filename": None}]
    pulados, posicoes_originais, itens_reais = partition_audio_batch_items(items)
    assert pulados == []
    assert posicoes_originais == [1]
    assert itens_reais == items
