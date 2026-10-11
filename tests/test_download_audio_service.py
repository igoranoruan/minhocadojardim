"""download/audio_service.py: download_audio_source ponta a ponta, com um PlatformDownloader
falso -- Parte A/B, autorização do CÉREBRO, 11/10/2026. Mesmo padrão de
tests/test_download_service.py, reaproveitando a MESMA FakeDownloader/convenções."""
from pathlib import Path

import pytest
from unittest.mock import patch

import download.audio_service as audio_service_module
from download.audio_service import NoAudioInPostError, download_audio_source
from download.base import PlatformDownloader, RawDownload
from download.errors import DownloadFailedError, InvalidFileError, NoVideoInPostError, VideoTooLargeError, VideoTooLongError
from download.platform import Platform
from download.result import DownloadResult
from download.url_safety import ValidatedUrl


class FakeAudioDownloader(PlatformDownloader):
    def __init__(self, platform, *, content=b"audio", extension="m4a", duration=None, error=None):
        self.platform = platform
        self.content = content
        self.extension = extension
        self.duration = duration
        self.error = error
        self.calls: list[str] = []

    def download(self, url: str, dest_stub: Path) -> RawDownload:
        self.calls.append(url)
        if self.error is not None:
            raise self.error
        path = dest_stub.with_suffix(f".{self.extension}")
        path.write_bytes(self.content)
        return RawDownload(path=path, duration_seconds=self.duration)


@pytest.fixture()
def registry(monkeypatch, tmp_path):
    fakes: dict[Platform, FakeAudioDownloader] = {p: FakeAudioDownloader(p) for p in Platform}
    monkeypatch.setattr(audio_service_module, "_AUDIO_DOWNLOADERS", fakes)
    monkeypatch.setattr("download.tempfiles.DOWNLOAD_TEMP_DIR", str(tmp_path))
    return fakes


def _sem_ssrf(monkeypatch):
    monkeypatch.setattr("download.url_safety.resolve_host_ips", lambda host: ("93.184.216.34",))


# ============================================================================ caminho feliz
def test_download_com_sucesso_marca_media_type_audio(registry, monkeypatch):
    _sem_ssrf(monkeypatch)
    registry[Platform.TIKTOK].duration = 15.0
    resultado = download_audio_source("https://www.tiktok.com/@a/video/1")
    assert isinstance(resultado, DownloadResult)
    assert resultado.media_type == "audio"
    assert resultado.platform is Platform.TIKTOK
    assert resultado.temp_path.exists()
    assert resultado.size_bytes == len(b"audio")
    assert resultado.duration_seconds == 15.0
    assert registry[Platform.TIKTOK].calls == ["https://www.tiktok.com/@a/video/1"]


@pytest.mark.parametrize("extensao", ["m4a", "opus", "ogg", "aac", "mp3", "wav", "mp4", "webm"])
def test_aceita_qualquer_extensao_de_audio_ou_video_completo(registry, monkeypatch, extensao):
    """"bestaudio/best" pode devolver um contêiner só-áudio (bestaudio) OU um vídeo completo
    (fallback "best") -- as duas famílias de extensão são aceitas."""
    _sem_ssrf(monkeypatch)
    registry[Platform.TIKTOK].extension = extensao
    resultado = download_audio_source("https://www.tiktok.com/@a/video/1")
    assert resultado.container_format == extensao


def test_extensao_nao_reconhecida_e_recusada_e_limpa(registry, monkeypatch, tmp_path):
    _sem_ssrf(monkeypatch)
    registry[Platform.TIKTOK].extension = "exe"
    with pytest.raises(InvalidFileError):
        download_audio_source("https://www.tiktok.com/@a/video/1")
    assert list(tmp_path.iterdir()) == []


# ============================================================================ validações pós-download (reaproveitadas)
def test_duracao_acima_do_limite_e_recusada_e_limpa(registry, monkeypatch, tmp_path):
    _sem_ssrf(monkeypatch)
    registry[Platform.YOUTUBE].duration = 301.0
    with pytest.raises(VideoTooLongError):
        download_audio_source("https://youtu.be/abc123")
    assert list(tmp_path.iterdir()) == []


def test_arquivo_maior_que_o_limite_e_recusado_e_limpo(registry, monkeypatch, tmp_path):
    _sem_ssrf(monkeypatch)
    monkeypatch.setattr("download.audio_service.MAX_VIDEO_SIZE_BYTES", 3)
    registry[Platform.TIKTOK].content = b"audio-grande"
    with pytest.raises(VideoTooLargeError):
        download_audio_source("https://www.tiktok.com/@a/video/1")
    assert list(tmp_path.iterdir()) == []


# ============================================================================ post sem vídeo NEM áudio
def test_post_sem_video_nem_audio_vira_mensagem_exata_sem_fallback_de_imagem(registry, monkeypatch, tmp_path):
    """Diferente do fluxo de vídeo (que tenta baixar a imagem), aqui não existe "áudio de uma
    imagem" -- NoVideoInPostError do downloader vira NoAudioInPostError direto."""
    _sem_ssrf(monkeypatch)
    registry[Platform.PINTEREST].error = NoVideoInPostError("só imagem")
    with pytest.raises(NoAudioInPostError) as exc_info:
        download_audio_source("https://pinterest.com/pin/123456789/")
    assert exc_info.value.user_message == "Esse vídeo não tem áudio."
    assert list(tmp_path.iterdir()) == []


# ============================================================================ erro do downloader propaga limpo
def test_erro_do_downloader_e_propagado_e_limpa_parciais(registry, monkeypatch, tmp_path):
    _sem_ssrf(monkeypatch)

    def baixa_e_falha(url, dest_stub):
        dest_stub.with_suffix(".m4a.part").write_bytes(b"parcial")
        raise DownloadFailedError("falha simulada")

    registry[Platform.TIKTOK].download = baixa_e_falha
    with pytest.raises(DownloadFailedError):
        download_audio_source("https://www.tiktok.com/@a/video/1")
    assert list(tmp_path.iterdir()) == []


def test_excecao_inesperada_vira_downloadfailederror_generico_sem_vazar_detalhe(registry, monkeypatch):
    _sem_ssrf(monkeypatch)

    def explode(url, dest_stub):
        raise RuntimeError("bug interno com detalhe sensível=segredo123")

    registry[Platform.TIKTOK].download = explode
    with pytest.raises(DownloadFailedError) as capturado:
        download_audio_source("https://www.tiktok.com/@a/video/1")
    assert "segredo123" not in capturado.value.user_message


# ============================================================================ links curtos (reaproveitado de download/service.py)
def _validated(url: str) -> ValidatedUrl:
    return ValidatedUrl(url=url, scheme="https", host=url.split("/")[2], port=443, resolved_ips=("93.184.216.34",))


def test_pin_it_chama_resolve_redirect_chain_e_envia_a_url_longa_ao_downloader(registry, monkeypatch):
    _sem_ssrf(monkeypatch)
    url_longa = "https://www.pinterest.com/pin/123456789/"
    with patch("download.audio_service.resolve_redirect_chain", return_value=_validated(url_longa)) as mock_resolve:
        resultado = download_audio_source("https://pin.it/5h9Mozm6x")
    mock_resolve.assert_called_once_with("https://pin.it/5h9Mozm6x")
    assert resultado.platform is Platform.PINTEREST
    assert registry[Platform.PINTEREST].calls == [url_longa]


@pytest.mark.parametrize("host_curto", ["vm.tiktok.com", "vt.tiktok.com"])
def test_tiktok_link_curto_chama_resolve_redirect_chain(registry, monkeypatch, host_curto):
    _sem_ssrf(monkeypatch)
    url_longa = "https://www.tiktok.com/@usuario/video/7659614093048909064"
    with patch("download.audio_service.resolve_redirect_chain", return_value=_validated(url_longa)) as mock_resolve:
        resultado = download_audio_source(f"https://{host_curto}/ZSb5f7NnW/")
    mock_resolve.assert_called_once_with(f"https://{host_curto}/ZSb5f7NnW/")
    assert resultado.platform is Platform.TIKTOK
    assert registry[Platform.TIKTOK].calls == [url_longa]


# ============================================================================ allowed_extractors nunca o genérico
@pytest.mark.parametrize("plataforma", list(Platform))
def test_allowed_extractors_nunca_usa_o_extractor_generico(plataforma):
    spec = audio_service_module._AUDIO_SPECS[plataforma]
    assert "generic" not in [e.lower() for e in spec.allowed_extractors]


def test_format_pedido_e_bestaudio_com_fallback_para_best():
    for spec in audio_service_module._AUDIO_SPECS.values():
        assert spec.extra_opts.get("format") == "bestaudio/best"


def test_tiktok_continua_com_impersonate_chrome():
    assert audio_service_module._AUDIO_SPECS[Platform.TIKTOK].extra_opts.get("impersonate") == "chrome"
