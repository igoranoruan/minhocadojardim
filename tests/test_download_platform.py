"""Detecção de plataforma: lista fechada de domínios, sem heurística e sem rede."""
import pytest

from download.errors import UnsupportedPlatformError
from download.platform import Platform, detect_platform, hosts_for

CASOS = [
    ("https://www.tiktok.com/@user/video/123", Platform.TIKTOK),
    ("https://vm.tiktok.com/ZM123/", Platform.TIKTOK),
    ("https://vt.tiktok.com/ZM123/", Platform.TIKTOK),
    ("https://instagram.com/reel/abc/", Platform.INSTAGRAM),
    ("https://www.instagram.com/reel/abc/", Platform.INSTAGRAM),
    ("https://pinterest.com/pin/123/", Platform.PINTEREST),
    ("https://pin.it/abc123", Platform.PINTEREST),
    ("https://www.youtube.com/watch?v=abc123", Platform.YOUTUBE),
    ("https://youtu.be/abc123", Platform.YOUTUBE),
    ("https://m.youtube.com/watch?v=abc123", Platform.YOUTUBE),
    ("https://music.youtube.com/watch?v=abc123", Platform.YOUTUBE),
]


@pytest.mark.parametrize("url, esperado", CASOS, ids=[c[0] for c in CASOS])
def test_deteccao_por_dominio(url, esperado):
    assert detect_platform(url) is esperado


def test_dominio_desconhecido_e_recusado():
    for url in ("https://example.com/video", "https://evil.example/tiktok.com", "https://youtube.com.evil.example/x"):
        with pytest.raises(UnsupportedPlatformError):
            detect_platform(url)


def test_deteccao_e_case_insensitive_no_host():
    assert detect_platform("https://WWW.TIKTOK.COM/@a/video/1") is Platform.TIKTOK


def test_url_sem_host():
    with pytest.raises(UnsupportedPlatformError):
        detect_platform("not-a-url")


def test_quatro_plataformas_tem_pelo_menos_um_dominio():
    for platform in Platform:
        assert len(hosts_for(platform)) >= 1
