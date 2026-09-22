"""Integração com yt-dlp usando mocks: nenhuma chamada real de rede.

Confirma: allowed_extractors nunca inclui o extractor genérico; max_filesize aplicado;
certificado nunca desligado; sem cookies pessoais; categorização de erro; localização do arquivo
de saída pela extensão real escolhida pelo yt-dlp; e o PO Token Provider (BGUTIL) só entra quando
configurado, e só para YouTube.
"""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yt_dlp

from download.errors import DownloadFailedError, DownloadTimeoutError, VideoTooLargeError
from download.platform import Platform
from download.ytdlp_downloader import YtDlpDownloader, YtDlpSpec


def _spec(platform=Platform.TIKTOK, extractors=("TikTok",), **extra):
    return YtDlpSpec(platform, allowed_extractors=extractors, extra_opts=extra.get("extra_opts", {}))


# ------------------------------------------------------------------ opções construídas
def test_allowed_extractors_nunca_pode_ser_o_generico():
    with pytest.raises(ValueError):
        YtDlpSpec(Platform.TIKTOK, allowed_extractors=("generic",))
    with pytest.raises(ValueError):
        YtDlpSpec(Platform.TIKTOK, allowed_extractors=("TikTok", "generic"))
    with pytest.raises(ValueError):
        YtDlpSpec(Platform.TIKTOK, allowed_extractors=("all",))


def test_opcoes_de_seguranca_nas_opcoes_construidas(tmp_path):
    downloader = YtDlpDownloader(_spec())
    options = downloader._build_options(tmp_path / "stub")
    assert options["allowed_extractors"] == ["TikTok"]
    assert "generic" not in [e.lower() for e in options["allowed_extractors"]]
    assert options["nocheckcertificate"] is False
    assert options["cookiefile"] is None
    assert options["max_filesize"] == 100 * 1024 * 1024
    assert options["noplaylist"] is True


def test_outtmpl_usa_o_stub_informado_sem_depender_de_metadados(tmp_path):
    stub = tmp_path / "abc123"
    downloader = YtDlpDownloader(_spec())
    options = downloader._build_options(stub)
    assert options["outtmpl"] == f"{stub}.%(ext)s"
    assert "%(title)s" not in options["outtmpl"]  # nunca deriva do título do vídeo


# ------------------------------------------------------------------ download (YoutubeDL mockado)
def _mock_ydl(info=None, side_effect=None):
    instance = MagicMock()
    instance.__enter__.return_value = instance
    instance.__exit__.return_value = False
    if side_effect is not None:
        instance.extract_info.side_effect = side_effect
    else:
        instance.extract_info.return_value = info or {}
    return instance


def test_download_com_sucesso_localiza_o_arquivo_e_le_a_duracao(tmp_path):
    stub = tmp_path / "abc123"
    (tmp_path / "abc123.mp4").write_bytes(b"video")
    downloader = YtDlpDownloader(_spec())
    with patch("download.ytdlp_downloader.yt_dlp.YoutubeDL", return_value=_mock_ydl({"duration": 42.0})):
        resultado = downloader.download("https://tiktok.com/@a/video/1", stub)
    assert resultado.path == tmp_path / "abc123.mp4"
    assert resultado.duration_seconds == 42.0


def test_download_sem_duracao_informada_devolve_none(tmp_path):
    stub = tmp_path / "semdur"
    (tmp_path / "semdur.mp4").write_bytes(b"video")
    downloader = YtDlpDownloader(_spec())
    with patch("download.ytdlp_downloader.yt_dlp.YoutubeDL", return_value=_mock_ydl({})):
        resultado = downloader.download("https://tiktok.com/@a/video/1", stub)
    assert resultado.duration_seconds is None


def test_nenhum_arquivo_de_saida_e_erro(tmp_path):
    stub = tmp_path / "nada"
    downloader = YtDlpDownloader(_spec())
    with patch("download.ytdlp_downloader.yt_dlp.YoutubeDL", return_value=_mock_ydl({})):
        with pytest.raises(DownloadFailedError):
            downloader.download("https://tiktok.com/@a/video/1", stub)


def test_arquivo_part_nao_e_considerado_saida_valida(tmp_path):
    stub = tmp_path / "parcial"
    (tmp_path / "parcial.mp4.part").write_bytes(b"x")
    downloader = YtDlpDownloader(_spec())
    with patch("download.ytdlp_downloader.yt_dlp.YoutubeDL", return_value=_mock_ydl({})):
        with pytest.raises(DownloadFailedError):
            downloader.download("https://tiktok.com/@a/video/1", stub)


@pytest.mark.parametrize(
    "mensagem, esperado",
    [
        ("ERROR: Requested content is not available, rate-limit reached or login required", DownloadFailedError),
        ("HTTP Error 403: max-filesize exceeded", VideoTooLargeError),
        ("ERROR: [download] The download has exceeded the specified filesize limit", VideoTooLargeError),
        ("urlopen error timed out", DownloadTimeoutError),
        ("ERROR: Unsupported URL", DownloadFailedError),
    ],
)
def test_categorizacao_de_erros_do_yt_dlp(tmp_path, mensagem, esperado):
    stub = tmp_path / "erro"
    downloader = YtDlpDownloader(_spec())
    erro_ydl = yt_dlp.utils.DownloadError(mensagem)
    with patch("download.ytdlp_downloader.yt_dlp.YoutubeDL", return_value=_mock_ydl(side_effect=erro_ydl)):
        with pytest.raises(esperado):
            downloader.download("https://tiktok.com/@a/video/1", stub)


def test_mensagem_crua_do_yt_dlp_nunca_chega_ao_usuario(tmp_path):
    stub = tmp_path / "erro2"
    downloader = YtDlpDownloader(_spec())
    erro_ydl = yt_dlp.utils.DownloadError("ERROR: https://tiktok.com/@fulano/video/999 unavailable")
    with patch("download.ytdlp_downloader.yt_dlp.YoutubeDL", return_value=_mock_ydl(side_effect=erro_ydl)):
        with pytest.raises(DownloadFailedError) as capturado:
            downloader.download("https://tiktok.com/@a/video/1", stub)
    assert "tiktok.com/@fulano" not in capturado.value.user_message


# ------------------------------------------------------------------ PO Token Provider (BGUTIL) — YouTube
def test_po_token_provider_desligado_por_padrao(tmp_path, use_settings):
    use_settings(bgutil_pot_provider_base_url="")
    downloader = YtDlpDownloader(_spec(Platform.YOUTUBE, ("Youtube",)))
    options = downloader._build_options(tmp_path / "stub")
    assert "youtubepot-bgutilhttp" not in options.get("extractor_args", {})


def test_po_token_provider_ligado_quando_configurado(tmp_path, use_settings):
    use_settings(bgutil_pot_provider_base_url="http://127.0.0.1:4416")
    downloader = YtDlpDownloader(_spec(Platform.YOUTUBE, ("Youtube",)))
    options = downloader._build_options(tmp_path / "stub")
    assert options["extractor_args"]["youtubepot-bgutilhttp"]["base_url"] == ["http://127.0.0.1:4416"]


def test_po_token_provider_nunca_e_aplicado_fora_do_youtube(tmp_path, use_settings):
    use_settings(bgutil_pot_provider_base_url="http://127.0.0.1:4416")
    downloader = YtDlpDownloader(_spec(Platform.TIKTOK, ("TikTok",)))
    options = downloader._build_options(tmp_path / "stub")
    assert "youtubepot-bgutilhttp" not in options.get("extractor_args", {})


def test_youtube_sempre_usa_o_player_client_mweb(tmp_path):
    downloader = YtDlpDownloader(YtDlpSpec(Platform.YOUTUBE, ("Youtube",), {"extractor_args": {"youtube": {"player_client": ["mweb"]}}}))
    options = downloader._build_options(tmp_path / "stub")
    assert options["extractor_args"]["youtube"]["player_client"] == ["mweb"]
