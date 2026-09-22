"""Validação básica do arquivo baixado: existência, extensão permitida e tamanho máximo."""
import pytest

from config import MAX_VIDEO_SIZE_BYTES
from download.errors import InvalidFileError, VideoTooLargeError
from download.file_validation import ALLOWED_EXTENSIONS, validate_downloaded_file


def test_arquivo_inexistente(tmp_path):
    with pytest.raises(InvalidFileError):
        validate_downloaded_file(tmp_path / "nao-existe.mp4")


def test_arquivo_vazio(tmp_path):
    arquivo = tmp_path / "vazio.mp4"
    arquivo.touch()
    with pytest.raises(InvalidFileError):
        validate_downloaded_file(arquivo)


@pytest.mark.parametrize("extensao", sorted(ALLOWED_EXTENSIONS))
def test_extensoes_permitidas_sao_aceitas(tmp_path, extensao):
    arquivo = tmp_path / f"video.{extensao}"
    arquivo.write_bytes(b"conteudo")
    assert validate_downloaded_file(arquivo) == len(b"conteudo")


@pytest.mark.parametrize("extensao", ["exe", "sh", "bat", "desktop", "php", "html", "part", "txt", ""])
def test_extensoes_nao_permitidas_sao_rejeitadas(tmp_path, extensao):
    nome = "arquivo" if extensao == "" else f"arquivo.{extensao}"
    arquivo = tmp_path / nome
    arquivo.write_bytes(b"conteudo")
    with pytest.raises(InvalidFileError):
        validate_downloaded_file(arquivo)


def test_arquivo_dentro_do_limite_e_aceito(tmp_path):
    arquivo = tmp_path / "video.mp4"
    arquivo.write_bytes(b"x" * 1024)
    assert validate_downloaded_file(arquivo) == 1024


def test_arquivo_maior_que_o_limite_e_rejeitado(tmp_path, monkeypatch):
    monkeypatch.setattr("download.file_validation.MAX_VIDEO_SIZE_BYTES", 100)
    arquivo = tmp_path / "video.mp4"
    arquivo.write_bytes(b"x" * 101)
    with pytest.raises(VideoTooLargeError):
        validate_downloaded_file(arquivo)


def test_arquivo_nao_e_apagado_pela_propria_validacao(tmp_path, monkeypatch):
    # A validação só CONSTATA o problema; quem apaga é o orquestrador (download/service.py).
    monkeypatch.setattr("download.file_validation.MAX_VIDEO_SIZE_BYTES", 10)
    arquivo = tmp_path / "video.mp4"
    arquivo.write_bytes(b"x" * 20)
    with pytest.raises(VideoTooLargeError):
        validate_downloaded_file(arquivo)
    assert arquivo.exists()


def test_limite_padrao_bate_com_100_mb():
    assert MAX_VIDEO_SIZE_BYTES == 100 * 1024 * 1024
