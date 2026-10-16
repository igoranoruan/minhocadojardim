"""processor/audio_service.py: process_audio ponta a ponta (FFmpeg real) + caminhos de erro
mockados -- Parte A/B, autorização do CÉREBRO, 11/10/2026. Mesmo padrão de
tests/test_processor_service.py."""
import subprocess
from unittest.mock import patch

import pytest

from helpers_processor import make_audio_only, make_video_no_audio, make_video_with_audio, make_video_with_metadata
from processor.audio_result import AudioProcessingResult
from processor.audio_service import process_audio
from processor.errors import InvalidOutputFileError, NoAudioStreamError, ProcessingTimeoutError, ProcessorError
from processor.probe import probe


@pytest.fixture(autouse=True)
def diretorio_de_processamento(tmp_path, monkeypatch):
    monkeypatch.setattr("processor.tempfiles.PROCESSING_TEMP_DIR", str(tmp_path / "processing"))


# ============================================================================ caminho feliz (FFmpeg real)
def test_processa_fonte_so_audio_com_sucesso(tmp_path):
    entrada = make_audio_only(tmp_path / "au.m4a", duration=1.0)
    resultado = process_audio(entrada)
    assert isinstance(resultado, AudioProcessingResult)
    assert resultado.output_path.exists()
    assert resultado.output_path.suffix == ".mp3"
    assert resultado.size_bytes > 0
    assert len(resultado.output_sha256) == 64


def test_processa_video_com_audio_extraindo_so_a_faixa(tmp_path):
    """Fonte com vídeo+áudio (fallback "best" do yt-dlp quando não há stream separado) -- a saída
    é só o MP3, sem nenhum stream de vídeo."""
    entrada = make_video_with_audio(tmp_path / "in.mp4", duration=1.0)
    resultado = process_audio(entrada)
    saida_probe = probe(resultado.output_path)
    assert saida_probe.has_audio is True
    assert saida_probe.has_video is False


def test_saida_e_mp3_192kbps_sem_metadados_de_origem(tmp_path):
    """"MP3 sem metadados" com ffprobe REAL (nunca mockado para esta checagem específica, pedido
    explícito do CÉREBRO) -- as tags injetadas na fonte não sobrevivem ao -map_metadata -1."""
    tags = {"title": "Titulo Sensivel", "artist": "Alguem", "comment": "info privada do usuario"}
    entrada = make_video_with_metadata(tmp_path / "in.mp4", duration=1.0, tags=tags)
    resultado = process_audio(entrada)

    texto = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format_tags", "-of", "default=noprint_wrappers=1",
         str(resultado.output_path)],
        capture_output=True, text=True,
    ).stdout
    for valor in tags.values():
        assert valor not in texto

    codec = subprocess.run(
        ["ffprobe", "-v", "quiet", "-select_streams", "a:0", "-show_entries", "stream=codec_name",
         "-of", "default=noprint_wrappers=1:nokey=1", str(resultado.output_path)],
        capture_output=True, text=True,
    ).stdout.strip()
    assert codec == "mp3"


def test_duracao_da_saida_bate_com_a_duracao_da_entrada(tmp_path):
    """Tolerância de 0.15s (15/10/2026, achado ao configurar o CI no GitHub Actions): o reencode
    AAC -> MP3 soma um padding/encoder-delay cuja duração exata varia entre builds/versões do
    FFmpeg (ex.: Windows local do Igor vs. Ubuntu do runner do Actions) -- comparar com
    igualdade exata depois de arredondar para 1 casa decimal falhava só por essa diferença de
    ambiente (2.1 vs 2.0), sem nenhum problema real no processamento. Uma diferença grande
    (ex.: áudio cortado pela metade) continua sendo pega por esta margem."""
    entrada = make_audio_only(tmp_path / "au.m4a", duration=2.0)
    entrada_probe = probe(entrada)
    resultado = process_audio(entrada)
    assert resultado.duration_seconds == pytest.approx(entrada_probe.duration_seconds or 0, abs=0.15)


def test_entrada_nao_e_apagada_pelo_processor(tmp_path):
    entrada = make_audio_only(tmp_path / "au.m4a", duration=1.0)
    process_audio(entrada)
    assert entrada.exists()


# ============================================================================ recusas antes do FFmpeg
def test_video_sem_audio_nao_chama_ffmpeg_e_mensagem_e_exata(tmp_path):
    entrada = make_video_no_audio(tmp_path / "mudo.mp4", duration=1.0)
    with patch("processor.audio_service.run_ffmpeg_audio") as mock_ffmpeg:
        with pytest.raises(NoAudioStreamError) as exc_info:
            process_audio(entrada)
    mock_ffmpeg.assert_not_called()
    assert exc_info.value.user_message == "Esse vídeo não tem áudio."


# ============================================================================ falhas depois do FFmpeg: limpeza garantida
def test_ffmpeg_produz_saida_invalida_e_e_limpa(tmp_path):
    entrada = make_audio_only(tmp_path / "au.m4a", duration=1.0)

    def fake_run_ffmpeg_audio(*, input_path, output_path, duration_seconds=None, on_progress=None):
        output_path.write_bytes(b"nao e um mp3 de verdade")

    with patch("processor.audio_service.run_ffmpeg_audio", side_effect=fake_run_ffmpeg_audio):
        with pytest.raises(InvalidOutputFileError):
            process_audio(entrada)

    processing_dir = tmp_path / "processing"
    assert not processing_dir.exists() or list(processing_dir.iterdir()) == []


def test_timeout_do_ffmpeg_propaga_e_limpa(tmp_path):
    entrada = make_audio_only(tmp_path / "au.m4a", duration=1.0)

    def fake_run_ffmpeg_audio(*, input_path, output_path, duration_seconds=None, on_progress=None):
        raise ProcessingTimeoutError("simulado")

    with patch("processor.audio_service.run_ffmpeg_audio", side_effect=fake_run_ffmpeg_audio):
        with pytest.raises(ProcessingTimeoutError):
            process_audio(entrada)

    processing_dir = tmp_path / "processing"
    assert not processing_dir.exists() or list(processing_dir.iterdir()) == []


def test_excecao_inesperada_vira_processorerror_generico_e_limpa(tmp_path):
    entrada = make_audio_only(tmp_path / "au.m4a", duration=1.0)

    def explode(*, input_path, output_path, duration_seconds=None, on_progress=None):
        raise RuntimeError("bug interno com detalhe sensível=segredo123")

    with patch("processor.audio_service.run_ffmpeg_audio", side_effect=explode):
        with pytest.raises(ProcessorError) as capturado:
            process_audio(entrada)
    assert "segredo123" not in capturado.value.user_message

    processing_dir = tmp_path / "processing"
    assert not processing_dir.exists() or list(processing_dir.iterdir()) == []


def test_sha256_so_e_calculado_apos_a_validacao_da_saida(tmp_path):
    entrada = make_audio_only(tmp_path / "au.m4a", duration=1.0)

    def fake_run_ffmpeg_audio(*, input_path, output_path, duration_seconds=None, on_progress=None):
        output_path.touch()  # arquivo vazio -> InvalidOutputFileError

    with patch("processor.audio_service.run_ffmpeg_audio", side_effect=fake_run_ffmpeg_audio):
        with patch("processor.audio_service._sha256_of") as mock_sha:
            with pytest.raises(InvalidOutputFileError):
                process_audio(entrada)
    mock_sha.assert_not_called()


def test_progresso_e_repassado_ao_callback(tmp_path):
    """FFmpeg real sempre emite um tick final "progress=end", que _percent_from_tick força para
    100.0 exatamente (ver processor/ffmpeg.py) -- não depende de nenhum tick intermediário, que
    pode ou não existir para um áudio de 1 segundo."""
    entrada = make_audio_only(tmp_path / "au.m4a", duration=1.0)
    percentuais = []
    process_audio(entrada, on_progress=percentuais.append)
    assert percentuais  # pelo menos o tick final
    assert percentuais[-1] == 100.0
