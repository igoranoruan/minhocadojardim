"""Orquestração ponta a ponta (URL -> validação -> plataforma -> downloader -> validação do
arquivo -> DownloadResult), usando um PlatformDownloader falso — nunca chama TikTok/Instagram/
Pinterest/YouTube de verdade.
"""
import time
from pathlib import Path
from unittest.mock import patch

import pytest

import download.service as service_module
from download.base import PlatformDownloader, RawDownload
from download.errors import (
    DownloadFailedError,
    DownloadTimeoutError,
    InvalidFileError,
    SsrfBlockedError,
    UnsupportedPlatformError,
    VideoTooLargeError,
    VideoTooLongError,
)
from download.platform import Platform
from download.result import DownloadResult
from download.service import download_video


class FakeDownloader(PlatformDownloader):
    """Downloader de mentira: grava bytes no stub, sem rede nem yt-dlp."""

    def __init__(self, platform, *, content=b"video", extension="mp4", duration=None, delay=0.0, error=None):
        self.platform = platform
        self.content = content
        self.extension = extension
        self.duration = duration
        self.delay = delay
        self.error = error
        self.calls: list[str] = []

    def download(self, url: str, dest_stub: Path) -> RawDownload:
        self.calls.append(url)
        if self.delay:
            time.sleep(self.delay)
        if self.error is not None:
            raise self.error
        path = dest_stub.with_suffix(f".{self.extension}")
        path.write_bytes(self.content)
        return RawDownload(path=path, duration_seconds=self.duration)


@pytest.fixture()
def registry(monkeypatch, tmp_path):
    """Substitui o registro de downloaders e o diretório temporário por uma versão de teste."""
    fakes: dict[Platform, FakeDownloader] = {p: FakeDownloader(p) for p in Platform}
    monkeypatch.setattr(service_module, "_DOWNLOADERS", fakes)
    monkeypatch.setattr("download.tempfiles.DOWNLOAD_TEMP_DIR", str(tmp_path))
    return fakes


def _sem_ssrf(monkeypatch):
    """As URLs de teste usam domínios reais (tiktok.com etc.); evitamos depender de DNS real."""
    monkeypatch.setattr("download.url_safety.resolve_host_ips", lambda host: ("93.184.216.34",))


# ------------------------------------------------------------------ caminho feliz
def test_download_com_sucesso(registry, monkeypatch):
    _sem_ssrf(monkeypatch)
    registry[Platform.TIKTOK].duration = 30.0
    resultado = download_video("https://www.tiktok.com/@a/video/1")
    assert isinstance(resultado, DownloadResult)
    assert resultado.platform is Platform.TIKTOK
    assert resultado.temp_path.exists()
    assert resultado.size_bytes == len(b"video")
    assert resultado.duration_seconds == 30.0
    assert resultado.container_format == "mp4"
    assert registry[Platform.TIKTOK].calls == ["https://www.tiktok.com/@a/video/1"]


def test_cada_plataforma_usa_o_downloader_correto(registry, monkeypatch):
    _sem_ssrf(monkeypatch)
    casos = {
        "https://www.tiktok.com/@a/video/1": Platform.TIKTOK,
        "https://instagram.com/reel/abc/": Platform.INSTAGRAM,
        "https://pinterest.com/pin/123/": Platform.PINTEREST,
        "https://youtu.be/abc123": Platform.YOUTUBE,
    }
    for url, plataforma in casos.items():
        resultado = download_video(url)
        assert resultado.platform is plataforma
        assert registry[plataforma].calls[-1] == url


# ------------------------------------------------------------------ recusas antes de chamar o downloader
def test_url_de_dominio_nao_suportado_nao_chama_nenhum_downloader(registry, monkeypatch):
    _sem_ssrf(monkeypatch)
    with pytest.raises(UnsupportedPlatformError):
        download_video("https://example.com/video")
    assert all(fake.calls == [] for fake in registry.values())


def test_url_com_ssrf_nao_chega_a_identificar_plataforma(registry, monkeypatch):
    with pytest.raises(SsrfBlockedError):
        download_video("http://169.254.169.254/tiktok.com")
    assert all(fake.calls == [] for fake in registry.values())


# ------------------------------------------------------------------ validações pós-download
def test_duracao_informada_acima_do_limite_e_recusada_e_arquivo_e_limpo(registry, monkeypatch, tmp_path):
    _sem_ssrf(monkeypatch)
    registry[Platform.YOUTUBE].duration = 301.0  # > 5 min
    with pytest.raises(VideoTooLongError):
        download_video("https://youtu.be/abc123")
    assert list(tmp_path.iterdir()) == []  # nada sobrou


def test_duracao_ausente_nao_bloqueia_o_download(registry, monkeypatch):
    _sem_ssrf(monkeypatch)
    registry[Platform.YOUTUBE].duration = None  # validação definitiva fica para a Etapa 6
    resultado = download_video("https://youtu.be/abc123")
    assert resultado.duration_seconds is None


def test_arquivo_maior_que_o_limite_e_recusado_e_limpo(registry, monkeypatch, tmp_path):
    _sem_ssrf(monkeypatch)
    monkeypatch.setattr("download.file_validation.MAX_VIDEO_SIZE_BYTES", 3)
    registry[Platform.TIKTOK].content = b"video-grande"
    with pytest.raises(VideoTooLargeError):
        download_video("https://www.tiktok.com/@a/video/1")
    assert list(tmp_path.iterdir()) == []


def test_extensao_de_saida_nao_permitida_e_recusada_e_limpa(registry, monkeypatch, tmp_path):
    _sem_ssrf(monkeypatch)
    registry[Platform.TIKTOK].extension = "exe"
    with pytest.raises(InvalidFileError):
        download_video("https://www.tiktok.com/@a/video/1")
    assert list(tmp_path.iterdir()) == []


# ------------------------------------------------------------------ erro do downloader propaga limpo
def test_erro_do_downloader_e_propagado_e_limpa_parciais(registry, monkeypatch, tmp_path):
    _sem_ssrf(monkeypatch)

    def baixa_e_falha(url, dest_stub):
        dest_stub.with_suffix(".mp4.part").write_bytes(b"parcial")
        raise DownloadFailedError("falha simulada")

    registry[Platform.TIKTOK].download = baixa_e_falha
    with pytest.raises(DownloadFailedError):
        download_video("https://www.tiktok.com/@a/video/1")
    assert list(tmp_path.iterdir()) == []  # o .part também foi removido


def test_excecao_inesperada_vira_downloadfailederror_generico(registry, monkeypatch):
    _sem_ssrf(monkeypatch)

    def explode(url, dest_stub):
        raise RuntimeError("bug interno com detalhe sensível=segredo123")

    registry[Platform.TIKTOK].download = explode
    with pytest.raises(DownloadFailedError) as capturado:
        download_video("https://www.tiktok.com/@a/video/1")
    assert "segredo123" not in capturado.value.user_message


# ------------------------------------------------------------------ timeout
def test_timeout_interrompe_a_espera_do_chamador(registry, monkeypatch):
    _sem_ssrf(monkeypatch)
    monkeypatch.setattr("download.service.DOWNLOAD_TIMEOUT_SECONDS", 0.1)
    registry[Platform.TIKTOK].delay = 2.0
    inicio = time.monotonic()
    with pytest.raises(DownloadTimeoutError):
        download_video("https://www.tiktok.com/@a/video/1")
    assert time.monotonic() - inicio < 1.0  # não esperou os 2s do downloader
