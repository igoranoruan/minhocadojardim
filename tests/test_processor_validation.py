"""Validação de entrada (tamanho, duração, stream de vídeo obrigatório, áudio opcional) e de
saída (tamanho, existência, stream de vídeo)."""
import pytest

from helpers_processor import make_audio_only, make_not_a_video, make_video_no_audio, make_video_with_audio
from processor.errors import (
    InputTooLargeError,
    InputTooLongError,
    InvalidInputFileError,
    InvalidOutputFileError,
    NoVideoStreamError,
    OutputTooLargeError,
)
from processor.validation import validate_input, validate_output


def test_entrada_valida_com_audio(tmp_path):
    caminho = make_video_with_audio(tmp_path / "a.mp4", duration=1.0)
    resultado = validate_input(caminho)
    assert resultado.has_video and resultado.has_audio


def test_entrada_valida_sem_audio_e_aceita_audio_e_opcional(tmp_path):
    caminho = make_video_no_audio(tmp_path / "v.mp4", duration=1.0)
    resultado = validate_input(caminho)
    assert resultado.has_video and not resultado.has_audio


def test_entrada_inexistente(tmp_path):
    with pytest.raises(InvalidInputFileError):
        validate_input(tmp_path / "nao-existe.mp4")


def test_entrada_vazia(tmp_path):
    arquivo = tmp_path / "vazio.mp4"
    arquivo.touch()
    with pytest.raises(InvalidInputFileError):
        validate_input(arquivo)


def test_entrada_maior_que_o_limite(tmp_path, monkeypatch):
    caminho = make_video_no_audio(tmp_path / "v.mp4", duration=1.0)
    monkeypatch.setattr("processor.validation.MAX_VIDEO_SIZE_BYTES", 10)
    with pytest.raises(InputTooLargeError):
        validate_input(caminho)


def test_entrada_mais_longa_que_o_limite(tmp_path, monkeypatch):
    caminho = make_video_no_audio(tmp_path / "v.mp4", duration=2.0)
    monkeypatch.setattr("processor.validation.MAX_VIDEO_DURATION_SECONDS", 1)
    with pytest.raises(InputTooLongError):
        validate_input(caminho)


def test_entrada_dentro_do_limite_de_duracao_passa(tmp_path, monkeypatch):
    caminho = make_video_no_audio(tmp_path / "v.mp4", duration=1.0)
    monkeypatch.setattr("processor.validation.MAX_VIDEO_DURATION_SECONDS", 5)
    validate_input(caminho)  # não levanta


def test_entrada_sem_stream_de_video_e_recusada(tmp_path):
    caminho = make_audio_only(tmp_path / "au.m4a", duration=1.0)
    with pytest.raises(NoVideoStreamError):
        validate_input(caminho)


def test_entrada_que_nao_e_video_de_verdade(tmp_path):
    caminho = make_not_a_video(tmp_path / "lixo.mp4")
    with pytest.raises(InvalidInputFileError):
        validate_input(caminho)


def test_saida_valida(tmp_path):
    caminho = make_video_with_audio(tmp_path / "s.mp4", duration=1.0)
    resultado = validate_output(caminho)
    assert resultado.has_video


def test_saida_inexistente(tmp_path):
    with pytest.raises(InvalidOutputFileError):
        validate_output(tmp_path / "nao-existe.mp4")


def test_saida_maior_que_o_limite(tmp_path, monkeypatch):
    caminho = make_video_no_audio(tmp_path / "s.mp4", duration=1.0)
    monkeypatch.setattr("processor.validation.MAX_VIDEO_SIZE_BYTES", 10)
    with pytest.raises(OutputTooLargeError):
        validate_output(caminho)


def test_saida_que_nao_e_video_de_verdade(tmp_path):
    caminho = make_not_a_video(tmp_path / "lixo.mp4")
    with pytest.raises(InvalidOutputFileError):
        validate_output(caminho)
