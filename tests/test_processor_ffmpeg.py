"""Construção dos argumentos do FFmpeg e execução real com timeout (fixtures sintéticos locais)."""
import time
from pathlib import Path

import pytest

from helpers_processor import make_video_no_audio, make_video_with_audio, make_video_with_metadata
from processor.errors import FfmpegFailedError, FfmpegUnavailableError, ProcessingTimeoutError
from processor.ffmpeg import build_args, run_ffmpeg
from processor.probe import probe


# ------------------------------------------------------------------ construção dos argumentos (sem executar)
def test_argumentos_com_audio():
    args = build_args(executable="ffmpeg", input_path=Path("/tmp/in.mp4"), output_path=Path("/tmp/out.mp4"), has_audio=True)
    assert args[0] == "ffmpeg"
    assert "-map" in args and args[args.index("-map") + 1] == "0:v:0"
    assert "0:a:0" in args  # áudio mapeado
    assert "-c:a" in args and args[args.index("-c:a") + 1] == "aac"
    assert "-c:v" in args and args[args.index("-c:v") + 1] == "libx264"
    assert "-map_metadata" in args and args[args.index("-map_metadata") + 1] == "-1"
    assert "-map_chapters" in args and args[args.index("-map_chapters") + 1] == "-1"
    assert "-movflags" in args and args[args.index("-movflags") + 1] == "+faststart"


def test_argumentos_sem_audio_nao_mapeia_nem_codifica_audio():
    args = build_args(executable="ffmpeg", input_path=Path("/tmp/in.mp4"), output_path=Path("/tmp/out.mp4"), has_audio=False)
    assert "0:a:0" not in args
    assert "-c:a" not in args


def test_argumentos_nunca_usam_shell():
    args = build_args(executable="ffmpeg", input_path=Path("/tmp/in.mp4"), output_path=Path("/tmp/out.mp4"), has_audio=True)
    assert isinstance(args, list) and all(isinstance(a, str) for a in args)


def test_caminhos_de_entrada_e_saida_sao_apenas_valores_nunca_flags():
    entrada, saida = Path("/tmp/--evil-flag"), Path("/tmp/out.mp4")
    args = build_args(executable="ffmpeg", input_path=entrada, output_path=saida, has_audio=False)
    # o caminho aparece como VALOR logo após -i, nunca interpretado como uma flag nova
    assert args[args.index("-i") + 1] == str(entrada)


def test_processor_ffmpeg_nunca_usa_shell_ou_os_system():
    fonte = Path("processor/ffmpeg.py").read_text(encoding="utf-8")
    assert "shell=True" not in fonte and "os.system(" not in fonte


# ------------------------------------------------------------------ execução real (fixtures sintéticos)
def test_run_ffmpeg_com_sucesso_com_audio(tmp_path):
    entrada = make_video_with_audio(tmp_path / "in.mp4", duration=1.0)
    saida = tmp_path / "out.mp4"
    run_ffmpeg(input_path=entrada, output_path=saida, has_audio=True)
    assert saida.exists() and saida.stat().st_size > 0
    resultado = probe(saida)
    assert resultado.video_codec == "h264" and resultado.audio_codec == "aac"


def test_run_ffmpeg_com_sucesso_sem_audio(tmp_path):
    entrada = make_video_no_audio(tmp_path / "in.mp4", duration=1.0)
    saida = tmp_path / "out.mp4"
    run_ffmpeg(input_path=entrada, output_path=saida, has_audio=False)
    resultado = probe(saida)
    assert resultado.has_video and not resultado.has_audio


def test_run_ffmpeg_remove_metadata_conhecida(tmp_path):
    tags = {"title": "Segredo Pessoal", "artist": "Fulano de Tal", "comment": "dado sensivel"}
    entrada = make_video_with_metadata(tmp_path / "in.mp4", duration=1.0, tags=tags)
    antes = probe(entrada)  # só confirma que o fixture tem os streams esperados
    assert antes.has_video and antes.has_audio

    saida = tmp_path / "out.mp4"
    run_ffmpeg(input_path=entrada, output_path=saida, has_audio=True)

    import subprocess
    texto = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format_tags", "-of", "default=noprint_wrappers=1", str(saida)],
        capture_output=True, text=True,
    ).stdout
    for chave, valor in tags.items():
        assert valor not in texto, f"a tag {chave!r} vazou para a saída"


def test_run_ffmpeg_entrada_inexistente_e_erro_padronizado(tmp_path):
    with pytest.raises(FfmpegFailedError):
        run_ffmpeg(input_path=tmp_path / "nao-existe.mp4", output_path=tmp_path / "out.mp4", has_audio=False)


def test_run_ffmpeg_binario_indisponivel(tmp_path, use_settings):
    entrada = make_video_no_audio(tmp_path / "in.mp4")  # gerado com o FFmpeg real, ANTES da troca
    use_settings(ffmpeg_path="ffmpeg-binario-inexistente-xyz")
    with pytest.raises(FfmpegUnavailableError):
        run_ffmpeg(input_path=entrada, output_path=tmp_path / "out.mp4", has_audio=False)


def test_run_ffmpeg_timeout_mata_o_processo_e_nao_deixa_orfao(tmp_path, monkeypatch, use_settings):
    """Processo real, único e lento, PORTÁVEL entre Windows e Linux: em vez de um script shell
    (`#!/bin/bash`) executado diretamente, o que no Windows falha antes mesmo de chegar a
    run_ffmpeg (WinError 193 — não é um executável Win32 válido) e de `pgrep` (inexistente no
    Windows), usamos o PRÓPRIO interpretador Python como o processo lento — um binário real,
    absoluto, existente e diretamente executável em qualquer sistema operacional, sem shell e sem
    depender de utilitários Unix.

    subprocess.Popen é interceptado só para trocar a lista de argumentos que seria enviada ao
    "ffmpeg" (as flags fixas de build_args, que só um binário ffmpeg de verdade entenderia) por
    um comando que dorme — o restante do fluxo (Popen -> communicate(timeout) -> kill() ->
    communicate/wait) é executado de verdade, em cima de um processo do sistema operacional real.
    A ausência de processo órfão é confirmada de forma portável com Popen.poll() (funciona
    identicamente em Windows e Linux), sem pgrep.
    """
    import subprocess
    import sys

    capturado: dict = {}
    popen_real = subprocess.Popen

    def popen_falso(args, **kwargs):
        # Ignora as flags de ffmpeg construídas por build_args; sobe, no lugar, um processo real
        # e único (o próprio Python) que dorme mais do que o timeout configurado no teste.
        processo = popen_real([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)
        capturado["processo"] = processo
        return processo

    monkeypatch.setattr("processor.ffmpeg.subprocess.Popen", popen_falso)
    monkeypatch.setattr("processor.ffmpeg.PROCESSING_TIMEOUT_SECONDS", 1)
    use_settings(ffmpeg_path=sys.executable)  # só precisa resolver para um binário real existente

    entrada = make_video_no_audio(tmp_path / "in.mp4")
    inicio = time.monotonic()
    with pytest.raises(ProcessingTimeoutError):
        run_ffmpeg(input_path=entrada, output_path=tmp_path / "out.mp4", has_audio=False)
    decorrido = time.monotonic() - inicio
    assert decorrido < 5  # não esperou os 30s do processo real

    assert "processo" in capturado
    assert capturado["processo"].poll() is not None, "processo ficou órfão (ainda rodando) após o timeout"
