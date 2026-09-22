"""ffprobe: duração, stream de vídeo, áudio opcional — tudo com fixtures sintéticos locais."""
import pytest

from helpers_processor import make_audio_only, make_not_a_video, make_video_no_audio, make_video_with_audio
from processor.errors import FfprobeInvalidOutputError, FfprobeUnavailableError
from processor.probe import probe, resolve_executable


def test_video_com_audio_e_reconhecido(tmp_path):
    caminho = make_video_with_audio(tmp_path / "a.mp4", duration=1.0)
    resultado = probe(caminho)
    assert resultado.has_video and resultado.has_audio
    assert resultado.video_codec == "h264" and resultado.audio_codec == "aac"
    assert resultado.duration_seconds == pytest.approx(1.0, abs=0.2)


def test_video_sem_audio_e_reconhecido(tmp_path):
    caminho = make_video_no_audio(tmp_path / "v.mp4", duration=1.0)
    resultado = probe(caminho)
    assert resultado.has_video and not resultado.has_audio
    assert resultado.audio_codec is None


def test_somente_audio_nao_tem_stream_de_video(tmp_path):
    caminho = make_audio_only(tmp_path / "au.m4a", duration=1.0)
    resultado = probe(caminho)
    assert not resultado.has_video and resultado.has_audio


def test_arquivo_que_nao_e_video_da_erro_claro_nao_traceback_bruto(tmp_path):
    caminho = make_not_a_video(tmp_path / "lixo.mp4")
    with pytest.raises(FfprobeInvalidOutputError):
        probe(caminho)


def test_resolve_executable_com_nome_usa_shutil_which(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda nome: "/usr/bin/ffprobe-fake" if nome == "ffprobe" else None)
    assert resolve_executable("ffprobe", what="ffprobe") == "/usr/bin/ffprobe-fake"


def test_resolve_executable_nome_nao_encontrado(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda nome: None)
    with pytest.raises(FfprobeUnavailableError):
        resolve_executable("ffprobe-que-nao-existe", what="ffprobe")


def test_resolve_executable_caminho_absoluto_valido(tmp_path):
    binario = tmp_path / "meu-ffprobe"
    binario.write_text("#!/bin/sh\n")
    assert resolve_executable(str(binario), what="ffprobe") == str(binario)


def test_resolve_executable_caminho_absoluto_inexistente(tmp_path):
    with pytest.raises(FfprobeUnavailableError):
        resolve_executable(str(tmp_path / "nao-existe"), what="ffprobe")


def test_ffprobe_indisponivel_da_erro_diagnosticavel(tmp_path, use_settings):
    use_settings(ffprobe_path="ffprobe-binario-inexistente-xyz")
    caminho = make_video_no_audio(tmp_path / "v.mp4")
    with pytest.raises(FfprobeUnavailableError):
        probe(caminho)


def test_probe_nunca_usa_shell():
    fonte = __import__("pathlib").Path("processor/probe.py").read_text(encoding="utf-8")
    assert "shell=True" not in fonte and "os.system(" not in fonte
