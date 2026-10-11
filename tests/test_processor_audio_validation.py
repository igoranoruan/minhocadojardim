"""processor/audio_validation.py: validate_audio_input/validate_audio_output -- Parte A/B,
autorização do CÉREBRO, 11/10/2026. Mesmo padrão (FFmpeg real via fixtures sintéticos) de
tests/test_processor_validation.py, mas SEM exigir stream de vídeo (exige áudio no lugar)."""
import pytest

from helpers_processor import make_audio_only, make_not_a_video, make_video_no_audio, make_video_with_audio
from processor.audio_validation import validate_audio_input, validate_audio_output
from processor.errors import (
    InputTooLargeError,
    InputTooLongError,
    InvalidInputFileError,
    InvalidOutputFileError,
    NoAudioStreamError,
)
from processor.probe import ProbeResult


# ============================================================================ validate_audio_input
def test_aceita_fonte_so_audio(tmp_path):
    entrada = make_audio_only(tmp_path / "au.m4a", duration=1.0)
    resultado = validate_audio_input(entrada)
    assert isinstance(resultado, ProbeResult)
    assert resultado.has_audio is True


def test_aceita_video_com_audio_tambem(tmp_path):
    """A fonte pode muito bem ter vídeo (quando a plataforma não ofereceu stream separado e
    "bestaudio/best" caiu para "best") -- só a AUSÊNCIA de áudio é recusada, vídeo é irrelevante."""
    entrada = make_video_with_audio(tmp_path / "in.mp4", duration=1.0)
    resultado = validate_audio_input(entrada)
    assert resultado.has_audio is True


def test_recusa_video_sem_nenhum_audio_com_mensagem_exata(tmp_path):
    entrada = make_video_no_audio(tmp_path / "mudo.mp4", duration=1.0)
    with pytest.raises(NoAudioStreamError) as exc_info:
        validate_audio_input(entrada)
    assert exc_info.value.user_message == "Esse vídeo não tem áudio."


def test_recusa_arquivo_que_nao_e_midia_valida(tmp_path):
    entrada = make_not_a_video(tmp_path / "lixo.mp4")
    with pytest.raises(InvalidInputFileError):
        validate_audio_input(entrada)


def test_recusa_arquivo_vazio(tmp_path):
    entrada = tmp_path / "vazio.mp3"
    entrada.touch()
    with pytest.raises(InvalidInputFileError):
        validate_audio_input(entrada)


def test_recusa_arquivo_inexistente(tmp_path):
    with pytest.raises(InvalidInputFileError):
        validate_audio_input(tmp_path / "nao-existe.mp3")


def test_recusa_entrada_maior_que_o_limite(tmp_path, monkeypatch):
    entrada = make_audio_only(tmp_path / "au.m4a", duration=1.0)
    monkeypatch.setattr("processor.audio_validation.MAX_VIDEO_SIZE_BYTES", 1)
    with pytest.raises(InputTooLargeError):
        validate_audio_input(entrada)


def test_recusa_entrada_mais_longa_que_o_limite(tmp_path, monkeypatch):
    entrada = make_audio_only(tmp_path / "au.m4a", duration=2.0)
    monkeypatch.setattr("processor.audio_validation.MAX_VIDEO_DURATION_SECONDS", 1)
    with pytest.raises(InputTooLongError):
        validate_audio_input(entrada)


# ============================================================================ validate_audio_output
def test_aceita_saida_mp3_valida(tmp_path):
    saida = make_audio_only(tmp_path / "saida.mp3", duration=1.0)
    resultado = validate_audio_output(saida)
    assert resultado.has_audio is True


def test_recusa_saida_sem_stream_de_audio(tmp_path):
    """Nunca esperado de verdade (processor/ffmpeg_audio.py só mapeia `-map 0:a:0`) -- defesa
    contra um bug de comando que produzisse um MP3 silencioso/corrompido."""
    saida = make_video_no_audio(tmp_path / "saida-mt-errada.mp4", duration=1.0)
    with pytest.raises(InvalidOutputFileError):
        validate_audio_output(saida)


def test_recusa_saida_vazia(tmp_path):
    saida = tmp_path / "saida-vazia.mp3"
    saida.touch()
    with pytest.raises(InvalidOutputFileError):
        validate_audio_output(saida)


def test_recusa_saida_maior_que_o_limite(tmp_path, monkeypatch):
    from processor.errors import OutputTooLargeError

    saida = make_audio_only(tmp_path / "saida.mp3", duration=1.0)
    monkeypatch.setattr("processor.audio_validation.MAX_VIDEO_SIZE_BYTES", 1)
    with pytest.raises(OutputTooLargeError):
        validate_audio_output(saida)
