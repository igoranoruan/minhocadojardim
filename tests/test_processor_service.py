"""Orquestração ponta a ponta (probe -> validação -> FFmpeg -> validação de saída -> SHA-256 ->
ProcessingResult), com fixtures sintéticos reais e também com FFmpeg substituído por mocks para
os caminhos de erro (padrão FakeDownloader da Etapa 5)."""
import hashlib
from unittest.mock import patch

import pytest

from helpers_processor import (
    make_audio_only,
    make_video_h264_com_audio_opus,
    make_video_no_audio,
    make_video_vp9_sem_audio,
    make_video_with_audio,
    make_video_with_metadata,
)
from processor.errors import (
    FfmpegFailedError,
    InputTooLongError,
    InvalidOutputFileError,
    NoVideoStreamError,
    ProcessingTimeoutError,
    ProcessorError,
)
from processor.probe import ProbeResult, probe
from processor.result import ProcessingResult
from processor.service import _escolher_modo_rapido, process_video


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


# ------------------------------------------------------------------ decisão pura (_escolher_modo_rapido) -- sem FFmpeg, só a regra
def _probe(*, video_codec, audio_codec, has_audio=True):
    return ProbeResult(
        duration_seconds=1.0, has_video=True, has_audio=has_audio,
        video_codec=video_codec, audio_codec=audio_codec if has_audio else None,
    )


def test_escolhe_copy_total_quando_video_h264_e_audio_aac():
    assert _escolher_modo_rapido(_probe(video_codec="h264", audio_codec="aac")) == "copy"


def test_escolhe_copy_total_quando_video_h264_e_sem_audio():
    assert _escolher_modo_rapido(_probe(video_codec="h264", audio_codec=None, has_audio=False)) == "copy"


def test_escolhe_caminho_intermediario_quando_video_h264_e_audio_opus():
    assert (
        _escolher_modo_rapido(_probe(video_codec="h264", audio_codec="opus"))
        == "copy_video_transcode_audio"
    )


def test_escolhe_transcode_total_quando_video_vp9_e_audio_aac():
    assert _escolher_modo_rapido(_probe(video_codec="vp9", audio_codec="aac")) == "transcode"


def test_escolhe_transcode_total_quando_video_vp9_e_audio_opus():
    assert _escolher_modo_rapido(_probe(video_codec="vp9", audio_codec="opus")) == "transcode"


# ------------------------------------------------------------------ cobertura explícita por plataforma (TikTok/Instagram/Pinterest, 30/09)
# processor/service.py NUNCA recebe a plataforma de origem -- _escolher_modo_rapido decide só a
# partir dos codecs reais do arquivo (probe), então a MESMA regra vale identicamente para as
# 4 plataformas (YouTube incluso). Estes testes existem para cobrir explicitamente, por nome, os
# cenários pedidos pelo CÉREBRO (TikTok/Instagram/Pinterest nos 3 modos) -- não porque o processor
# trate cada plataforma diferente (quem trata diferente é só o SELETOR DE FORMAT, em
# download/service.py, testado à parte em tests/test_download_service.py), mas para que a
# evidência de cobertura fique nomeada por plataforma, como pedido.
@pytest.mark.parametrize("plataforma", ["tiktok", "instagram", "pinterest"])
def test_video_h264_aac_usa_copy_total_qualquer_que_seja_a_plataforma_de_origem(tmp_path, plataforma):
    """Equivalente, para TikTok/Instagram/Pinterest, ao já provado para o YouTube: um vídeo que
    chega já H.264+AAC (independente de qual plataforma o entregou assim) usa o caminho `copy`
    total -- nada é reencodado."""
    entrada = make_video_with_audio(tmp_path / f"{plataforma}.mp4", duration=1.0)
    with patch("processor.service.run_ffmpeg", wraps=__import__("processor.ffmpeg", fromlist=["run_ffmpeg"]).run_ffmpeg) as espiao:
        resultado = process_video(entrada)
        assert espiao.call_args.kwargs["mode"] == "copy", plataforma
    saida_probe = probe(resultado.output_path)
    assert saida_probe.video_codec == "h264" and saida_probe.audio_codec == "aac"


@pytest.mark.parametrize("plataforma", ["tiktok", "instagram", "pinterest"])
def test_video_h264_audio_incompativel_usa_caminho_intermediario_qualquer_que_seja_a_plataforma(tmp_path, plataforma):
    """Equivalente, para TikTok/Instagram/Pinterest, ao cenário real que motivou esta rodada: um
    vídeo H.264 com áudio não-AAC usa `copy_video_transcode_audio` -- vídeo copiado, só o áudio
    reencodado -- nunca o transcode completo."""
    entrada = make_video_h264_com_audio_opus(tmp_path / f"{plataforma}.mp4", duration=1.0)
    with patch("processor.service.run_ffmpeg", wraps=__import__("processor.ffmpeg", fromlist=["run_ffmpeg"]).run_ffmpeg) as espiao:
        resultado = process_video(entrada)
        assert espiao.call_args.kwargs["mode"] == "copy_video_transcode_audio", plataforma
    saida_probe = probe(resultado.output_path)
    assert saida_probe.video_codec == "h264" and saida_probe.audio_codec == "aac"


@pytest.mark.parametrize("plataforma", ["tiktok", "instagram", "pinterest"])
def test_video_codec_incompativel_usa_transcode_completo_qualquer_que_seja_a_plataforma(tmp_path, plataforma):
    """Equivalente, para TikTok/Instagram/Pinterest, à causa raiz real do TikTok (geração 51):
    quando o vídeo de origem não é H.264 (ex.: bytevc1 no TikTok, representado aqui por VP9 --
    qualquer codec fora de _COPY_SAFE_VIDEO_CODECS produz a MESMA decisão), o transcode completo
    continua sendo o último recurso -- nunca falha a geração, só é mais lento que os caminhos
    rápidos."""
    entrada = make_video_vp9_sem_audio(tmp_path / f"{plataforma}.webm", duration=1.0)
    with patch("processor.service.run_ffmpeg", wraps=__import__("processor.ffmpeg", fromlist=["run_ffmpeg"]).run_ffmpeg) as espiao:
        resultado = process_video(entrada)
        assert espiao.call_args.kwargs["mode"] == "transcode", plataforma
    saida_probe = probe(resultado.output_path)
    assert saida_probe.video_codec == "h264"  # continua produzindo o MP4 h264 de sempre


# ------------------------------------------------------------------ caminho rápido (stream copy) -- otimização de performance
def test_process_video_usa_caminho_rapido_quando_entrada_ja_e_h264_aac(tmp_path):
    """Entrada JÁ h264/aac (make_video_with_audio) -- prova real de ponta a ponta: a saída
    continua MP4, com os MESMOS codecs/duração, e SEM passar pelo encoder (mode="copy" real)."""
    entrada = make_video_with_audio(tmp_path / "in.mp4", duration=1.0)
    entrada_probe = probe(entrada)

    with patch("processor.service.run_ffmpeg", wraps=__import__("processor.ffmpeg", fromlist=["run_ffmpeg"]).run_ffmpeg) as espiao:
        resultado = process_video(entrada)
        assert espiao.call_args.kwargs["mode"] == "copy"  # confirma que o caminho rápido foi usado

    saida_probe = probe(resultado.output_path)
    assert saida_probe.video_codec == "h264" and saida_probe.audio_codec == "aac"
    assert round(saida_probe.duration_seconds or 0, 1) == round(entrada_probe.duration_seconds or 0, 1)
    assert saida_probe.has_video and saida_probe.has_audio  # streams desejados preservados


def test_process_video_usa_caminho_intermediario_quando_so_audio_nao_e_compativel(tmp_path):
    """Entrada H.264 (copiável) + áudio Opus (NÃO copiável) -- otimização de performance, 30/09,
    aprovação do CÉREBRO: prova real de ponta a ponta que o vídeo é COPIADO (não passa pelo
    encoder libx264) e só o áudio é reencodado para AAC -- nunca o transcode completo, que
    reencodaria o vídeo à toa."""
    entrada = make_video_h264_com_audio_opus(tmp_path / "in.mp4", duration=1.0)
    entrada_probe = probe(entrada)
    assert entrada_probe.video_codec == "h264" and entrada_probe.audio_codec == "opus"

    with patch("processor.service.run_ffmpeg", wraps=__import__("processor.ffmpeg", fromlist=["run_ffmpeg"]).run_ffmpeg) as espiao:
        resultado = process_video(entrada)
        assert espiao.call_args.kwargs["mode"] == "copy_video_transcode_audio"

    saida_probe = probe(resultado.output_path)
    assert saida_probe.video_codec == "h264" and saida_probe.audio_codec == "aac"
    assert round(saida_probe.duration_seconds or 0, 1) == round(entrada_probe.duration_seconds or 0, 1)


def test_process_video_cai_para_transcode_quando_entrada_nao_e_compativel(tmp_path):
    """Entrada em VP9 (nunca elegível para o caminho rápido) -- continua indo direto para
    transcodificação, exatamente como antes desta etapa."""
    entrada = make_video_vp9_sem_audio(tmp_path / "in.webm", duration=1.0)

    with patch("processor.service.run_ffmpeg", wraps=__import__("processor.ffmpeg", fromlist=["run_ffmpeg"]).run_ffmpeg) as espiao:
        resultado = process_video(entrada)
        assert espiao.call_count == 1
        assert espiao.call_args.kwargs["mode"] == "transcode"

    saida_probe = probe(resultado.output_path)
    assert saida_probe.video_codec == "h264"  # continua produzindo o MP4 h264 de sempre


def test_process_video_cai_para_transcode_quando_copy_falha_mesmo_com_codecs_compativeis(tmp_path):
    """Mesmo quando o probe diz que os codecs são compatíveis, se a tentativa "copy" falhar de
    verdade (FfmpegFailedError), o processamento cai para transcodificação em vez de falhar a
    geração inteira."""
    from processor.ffmpeg import run_ffmpeg as run_ffmpeg_real

    entrada = make_video_with_audio(tmp_path / "in.mp4", duration=1.0)
    chamadas = []

    def fake_run_ffmpeg(*, input_path, output_path, has_audio, duration_seconds=None, on_progress=None, mode="transcode"):
        chamadas.append(mode)
        if mode == "copy":
            raise FfmpegFailedError("falha simulada no caminho rápido")
        return run_ffmpeg_real(
            input_path=input_path, output_path=output_path, has_audio=has_audio,
            duration_seconds=duration_seconds, on_progress=on_progress, mode=mode,
        )

    with patch("processor.service.run_ffmpeg", side_effect=fake_run_ffmpeg):
        resultado = process_video(entrada)
    assert chamadas == ["copy", "transcode"]
    assert isinstance(resultado, ProcessingResult) and resultado.output_path.exists()


def test_process_video_cai_para_transcode_quando_caminho_intermediario_falha(tmp_path):
    """Mesmo padrão de segurança do "copy" total, agora para o caminho intermediário: se
    "copy_video_transcode_audio" falhar de verdade (FfmpegFailedError), cai para
    transcodificação completa em vez de falhar a geração inteira."""
    from processor.ffmpeg import run_ffmpeg as run_ffmpeg_real

    entrada = make_video_h264_com_audio_opus(tmp_path / "in.mp4", duration=1.0)
    chamadas = []

    def fake_run_ffmpeg(*, input_path, output_path, has_audio, duration_seconds=None, on_progress=None, mode="transcode"):
        chamadas.append(mode)
        if mode == "copy_video_transcode_audio":
            raise FfmpegFailedError("falha simulada no caminho intermediário")
        return run_ffmpeg_real(
            input_path=input_path, output_path=output_path, has_audio=has_audio,
            duration_seconds=duration_seconds, on_progress=on_progress, mode=mode,
        )

    with patch("processor.service.run_ffmpeg", side_effect=fake_run_ffmpeg):
        resultado = process_video(entrada)
    assert chamadas == ["copy_video_transcode_audio", "transcode"]
    assert isinstance(resultado, ProcessingResult) and resultado.output_path.exists()


def test_process_video_timeout_no_caminho_intermediario_nao_tenta_transcode_depois(tmp_path):
    """Mesma regra de nunca dobrar a espera depois de um timeout, agora para o caminho
    intermediário: propaga IMEDIATAMENTE, sem tentar transcodificar em seguida."""
    entrada = make_video_h264_com_audio_opus(tmp_path / "in.mp4", duration=1.0)
    chamadas = []

    def fake_run_ffmpeg(*, input_path, output_path, has_audio, duration_seconds=None, on_progress=None, mode="transcode"):
        chamadas.append(mode)
        raise ProcessingTimeoutError("simulado")

    with patch("processor.service.run_ffmpeg", side_effect=fake_run_ffmpeg):
        with pytest.raises(ProcessingTimeoutError):
            process_video(entrada)
    assert chamadas == ["copy_video_transcode_audio"]  # nenhuma segunda tentativa


def test_process_video_timeout_no_caminho_rapido_nao_tenta_transcode_depois(tmp_path):
    """Um timeout na tentativa "copy" propaga IMEDIATAMENTE -- nunca tenta transcodificar depois
    (dobraria a espera até muito provavelmente travar de novo, ver docstring de process_video)."""
    entrada = make_video_with_audio(tmp_path / "in.mp4", duration=1.0)
    chamadas = []

    def fake_run_ffmpeg(*, input_path, output_path, has_audio, duration_seconds=None, on_progress=None, mode="transcode"):
        chamadas.append(mode)
        raise ProcessingTimeoutError("simulado")

    with patch("processor.service.run_ffmpeg", side_effect=fake_run_ffmpeg):
        with pytest.raises(ProcessingTimeoutError):
            process_video(entrada)
    assert chamadas == ["copy"]  # nenhuma segunda tentativa


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

    def fake_run_ffmpeg(*, input_path, output_path, has_audio, duration_seconds=None, on_progress=None, mode="transcode"):
        output_path.write_bytes(b"nao e um mp4 de verdade")  # simula saída corrompida

    with patch("processor.service.run_ffmpeg", side_effect=fake_run_ffmpeg):
        with pytest.raises(InvalidOutputFileError):
            process_video(entrada)

    processing_dir = tmp_path / "processing"
    assert not processing_dir.exists() or list(processing_dir.iterdir()) == []  # nada sobrou


def test_timeout_do_ffmpeg_propaga_e_limpa(tmp_path):
    entrada = make_video_no_audio(tmp_path / "in.mp4", duration=1.0)

    def fake_run_ffmpeg(*, input_path, output_path, has_audio, duration_seconds=None, on_progress=None, mode="transcode"):
        raise ProcessingTimeoutError("simulado")

    with patch("processor.service.run_ffmpeg", side_effect=fake_run_ffmpeg):
        with pytest.raises(ProcessingTimeoutError):
            process_video(entrada)

    processing_dir = tmp_path / "processing"
    assert not processing_dir.exists() or list(processing_dir.iterdir()) == []


def test_excecao_inesperada_vira_processorerror_generico_e_limpa(tmp_path):
    entrada = make_video_no_audio(tmp_path / "in.mp4", duration=1.0)

    def explode(*, input_path, output_path, has_audio, duration_seconds=None, on_progress=None, mode="transcode"):
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

    def fake_run_ffmpeg(*, input_path, output_path, has_audio, duration_seconds=None, on_progress=None, mode="transcode"):
        output_path.touch()  # arquivo vazio -> InvalidOutputFileError

    with patch("processor.service.run_ffmpeg", side_effect=fake_run_ffmpeg):
        with patch("processor.service._sha256_of") as mock_sha:
            with pytest.raises(InvalidOutputFileError):
                process_video(entrada)
    mock_sha.assert_not_called()
