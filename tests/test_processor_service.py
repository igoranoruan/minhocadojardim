"""Orquestração ponta a ponta (probe -> validação -> FFmpeg -> validação de saída -> SHA-256 ->
ProcessingResult), com fixtures sintéticos reais e também com FFmpeg substituído por mocks para
os caminhos de erro (padrão FakeDownloader da Etapa 5)."""
import hashlib
from unittest.mock import patch

import pytest

from helpers_processor import make_audio_only, make_video_no_audio, make_video_with_audio, make_video_with_metadata
from processor.errors import (
    InputTooLongError,
    InvalidOutputFileError,
    NoVideoStreamError,
    ProcessingTimeoutError,
    ProcessorError,
)
from processor.result import ProcessingResult
from processor.service import process_video


@pytest.fixture(autouse=True)
def diretorio_de_processamento(tmp_path, monkeypatch):
    monkeypatch.setattr("processor.tempfiles.PROCESSING_TEMP_DIR", str(tmp_path / "processing"))


# ------------------------------------------------------------------ caminho feliz (FFmpeg real)
def test_processamento_com_sucesso_com_audio(tmp_path):
    entrada = make_video_with_audio(tmp_path / "in.mp4", duration=1.0)
    resultado = process_video(entrada)
    assert isinstance(resultado, ProcessingResult)
    assert resultado.has_audio and resultado.size_bytes > 0
    assert resultado.output_path.exists()
    assert len(resultado.output_sha256) == 64


def test_processamento_com_sucesso_sem_audio(tmp_path):
    entrada = make_video_no_audio(tmp_path / "in.mp4", duration=1.0)
    resultado = process_video(entrada)
    assert not resultado.has_audio


def test_output_sha256_bate_com_o_arquivo_real_em_disco(tmp_path):
    entrada = make_video_with_audio(tmp_path / "in.mp4", duration=1.0)
    resultado = process_video(entrada)
    assert resultado.output_sha256 == hashlib.sha256(resultado.output_path.read_bytes()).hexdigest()


def test_metadata_sensivel_nao_sobrevive_ao_processamento(tmp_path):
    tags = {"title": "Titulo Sensivel", "artist": "Alguem", "comment": "info privada"}
    entrada = make_video_with_metadata(tmp_path / "in.mp4", duration=1.0, tags=tags)
    resultado = process_video(entrada)

    import subprocess
    texto = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format_tags", "-of", "default=noprint_wrappers=1",
         str(resultado.output_path)],
        capture_output=True, text=True,
    ).stdout
    for valor in tags.values():
        assert valor not in texto


def test_entrada_nao_e_apagada_pelo_processor(tmp_path):
    entrada = make_video_no_audio(tmp_path / "in.mp4", duration=1.0)
    process_video(entrada)
    assert entrada.exists()  # o processor nunca apaga o arquivo de ENTRADA (é da Etapa 5)


# ------------------------------------------------------------------ recusas antes do FFmpeg
def test_entrada_sem_stream_de_video_nao_chama_ffmpeg(tmp_path):
    entrada = make_audio_only(tmp_path / "au.m4a", duration=1.0)
    with patch("processor.service.run_ffmpeg") as mock_ffmpeg:
        with pytest.raises(NoVideoStreamError):
            process_video(entrada)
    mock_ffmpeg.assert_not_called()


def test_entrada_longa_demais_nao_chama_ffmpeg(tmp_path, monkeypatch):
    entrada = make_video_no_audio(tmp_path / "in.mp4", duration=2.0)
    monkeypatch.setattr("processor.validation.MAX_VIDEO_DURATION_SECONDS", 1)
    with patch("processor.service.run_ffmpeg") as mock_ffmpeg:
        with pytest.raises(InputTooLongError):
            process_video(entrada)
    mock_ffmpeg.assert_not_called()


# ------------------------------------------------------------------ falhas depois do FFmpeg: limpeza garantida
def test_ffmpeg_produz_saida_invalida_e_e_limpa(tmp_path):
    entrada = make_video_no_audio(tmp_path / "in.mp4", duration=1.0)

    def fake_run_ffmpeg(*, input_path, output_path, has_audio):
        output_path.write_bytes(b"nao e um mp4 de verdade")  # simula saída corrompida

    with patch("processor.service.run_ffmpeg", side_effect=fake_run_ffmpeg):
        with pytest.raises(InvalidOutputFileError):
            process_video(entrada)

    processing_dir = tmp_path / "processing"
    assert not processing_dir.exists() or list(processing_dir.iterdir()) == []  # nada sobrou


def test_timeout_do_ffmpeg_propaga_e_limpa(tmp_path):
    entrada = make_video_no_audio(tmp_path / "in.mp4", duration=1.0)

    def fake_run_ffmpeg(*, input_path, output_path, has_audio):
        raise ProcessingTimeoutError("simulado")

    with patch("processor.service.run_ffmpeg", side_effect=fake_run_ffmpeg):
        with pytest.raises(ProcessingTimeoutError):
            process_video(entrada)

    processing_dir = tmp_path / "processing"
    assert not processing_dir.exists() or list(processing_dir.iterdir()) == []


def test_excecao_inesperada_vira_processorerror_generico_e_limpa(tmp_path):
    entrada = make_video_no_audio(tmp_path / "in.mp4", duration=1.0)

    def explode(*, input_path, output_path, has_audio):
        raise RuntimeError("bug interno com detalhe sensível=segredo123")

    with patch("processor.service.run_ffmpeg", side_effect=explode):
        with pytest.raises(ProcessorError) as capturado:
            process_video(entrada)
    assert "segredo123" not in capturado.value.user_message

    processing_dir = tmp_path / "processing"
    assert not processing_dir.exists() or list(processing_dir.iterdir()) == []


def test_sha256_so_e_calculado_apos_a_validacao_da_saida(tmp_path):
    """Se a validação da saída falhar, o processamento não deve sequer tentar ler o arquivo
    inteiro para hash (a validação vem antes, na ordem do código)."""
    entrada = make_video_no_audio(tmp_path / "in.mp4", duration=1.0)

    def fake_run_ffmpeg(*, input_path, output_path, has_audio):
        output_path.touch()  # arquivo vazio -> InvalidOutputFileError

    with patch("processor.service.run_ffmpeg", side_effect=fake_run_ffmpeg):
        with patch("processor.service._sha256_of") as mock_sha:
            with pytest.raises(InvalidOutputFileError):
                process_video(entrada)
    mock_sha.assert_not_called()
