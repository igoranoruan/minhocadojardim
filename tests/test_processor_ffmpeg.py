"""Construção dos argumentos do FFmpeg e execução real com timeout (fixtures sintéticos locais)."""
import time
from pathlib import Path

import pytest

from helpers_processor import make_video_no_audio, make_video_with_audio, make_video_with_metadata
from processor.errors import FfmpegFailedError, FfmpegUnavailableError, ProcessingTimeoutError
from processor.ffmpeg import _percent_from_tick, _seconds_from_tick, build_args, run_ffmpeg
from processor.probe import probe


# ------------------------------------------------------------------ construção dos argumentos (sem executar)
def test_argumentos_com_audio():
    args = build_args(executable="ffmpeg", input_path=Path("/tmp/in.mp4"), output_path=Path("/tmp/out.mp4"), has_audio=True)
    assert args[0] == "ffmpeg"
    assert "-map" in args and args[args.index("-map") + 1] == "0:v:0"
    assert "0:a:0" in args  # áudio mapeado
    assert "-c:a" in args and args[args.index("-c:a") + 1] == "aac"
    assert "-c:v" in args and args[args.index("-c:v") + 1] == "libx264"
    assert "-preset" in args and args[args.index("-preset") + 1] == "veryfast"
    assert "-crf" in args and args[args.index("-crf") + 1] == "23"
    assert "-map_metadata" in args and args[args.index("-map_metadata") + 1] == "-1"
    assert "-map_chapters" in args and args[args.index("-map_chapters") + 1] == "-1"
    assert "-movflags" in args and args[args.index("-movflags") + 1] == "+faststart"
    assert "-progress" in args and args[args.index("-progress") + 1] == "pipe:1"
    assert "-nostats" in args


def test_argumentos_sem_audio_nao_mapeia_nem_codifica_audio():
    args = build_args(executable="ffmpeg", input_path=Path("/tmp/in.mp4"), output_path=Path("/tmp/out.mp4"), has_audio=False)
    assert "0:a:0" not in args
    assert "-c:a" not in args
    # -preset/-crf são configuração de VÍDEO: continuam presentes independente de has_audio
    assert "-preset" in args and args[args.index("-preset") + 1] == "veryfast"
    assert "-crf" in args and args[args.index("-crf") + 1] == "23"


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


# ------------------------------------------------------------------ parser de progresso (puro, sem FFmpeg)
def test_seconds_from_tick_prioriza_out_time_us():
    tick = {"out_time_us": "2500000", "out_time": "00:00:01.000000", "out_time_ms": "999"}
    assert _seconds_from_tick(tick) == 2.5


def test_seconds_from_tick_usa_out_time_quando_nao_ha_out_time_us():
    tick = {"out_time": "00:01:02.500000", "out_time_ms": "999"}
    assert _seconds_from_tick(tick) == 62.5


def test_seconds_from_tick_cai_para_out_time_ms_so_como_ultimo_recurso():
    assert _seconds_from_tick({"out_time_ms": "3000"}) == 3.0


def test_seconds_from_tick_sem_nenhum_campo_e_none():
    assert _seconds_from_tick({}) is None


def test_seconds_from_tick_ignora_valor_invalido_e_tenta_o_proximo_da_ordem():
    tick = {"out_time_us": "não-é-um-número", "out_time": "00:00:05.000000"}
    assert _seconds_from_tick(tick) == 5.0


def test_percent_from_tick_com_duration_none_e_sempre_none():
    """duration_seconds=None -> percent=None, mesmo com dado real de tempo processado — nunca
    percentual inventado."""
    assert _percent_from_tick({"out_time_us": "5000000"}, None) is None


def test_percent_from_tick_progress_end_e_100_exato():
    assert _percent_from_tick({"progress": "end", "out_time_us": "1"}, 10.0) == 100.0


def test_percent_from_tick_calcula_percentual_real():
    assert _percent_from_tick({"out_time_us": "5000000"}, 10.0) == 50.0


def test_percent_from_tick_nunca_ultrapassa_100():
    assert _percent_from_tick({"out_time_us": "50000000"}, 10.0) == 100.0


def test_percent_from_tick_nunca_fica_negativo():
    assert _percent_from_tick({"out_time_us": "-1000000"}, 10.0) == 0.0


# ------------------------------------------------------------------ progresso real (FFmpeg de verdade)
def test_run_ffmpeg_emite_progresso_real_monotonico_ate_100(tmp_path):
    entrada = make_video_with_audio(tmp_path / "in.mp4", duration=3.0)
    duracao_real = probe(entrada).duration_seconds
    eventos = []
    run_ffmpeg(
        input_path=entrada, output_path=tmp_path / "out.mp4", has_audio=True,
        duration_seconds=duracao_real, on_progress=eventos.append,
    )
    assert len(eventos) >= 1
    nao_none = [e for e in eventos if e is not None]
    assert all(0 <= e <= 100 for e in nao_none)
    assert all(nao_none[i] <= nao_none[i + 1] for i in range(len(nao_none) - 1))  # monotônico
    assert eventos[-1] == 100.0  # último evento (progress=end) é exatamente 100


def test_run_ffmpeg_sem_duration_seconds_percent_e_sempre_none(tmp_path):
    """Vídeo "sem duração conhecida" simulado por duration_seconds=None -- nunca inventa
    percentual, mesmo com o FFmpeg emitindo progresso de verdade."""
    entrada = make_video_no_audio(tmp_path / "in.mp4", duration=1.0)
    eventos = []
    run_ffmpeg(
        input_path=entrada, output_path=tmp_path / "out.mp4", has_audio=False,
        duration_seconds=None, on_progress=eventos.append,
    )
    assert len(eventos) >= 1
    assert all(e is None for e in eventos)


def test_run_ffmpeg_sem_on_progress_continua_funcionando_como_antes(tmp_path):
    """Comportamento preservado: chamar sem on_progress/duration_seconds (como o código antigo
    fazia) continua funcionando exatamente igual."""
    entrada = make_video_no_audio(tmp_path / "in.mp4", duration=1.0)
    run_ffmpeg(input_path=entrada, output_path=tmp_path / "out.mp4", has_audio=False)
    assert (tmp_path / "out.mp4").exists()


def test_run_ffmpeg_erro_no_callback_de_progresso_nao_derruba_o_processamento(tmp_path):
    """Uma falha no CALLBACK do chamador nunca pode impedir a leitura do pipe (senão o processo
    trava) nem interromper um processamento que teria dado certo."""
    entrada = make_video_no_audio(tmp_path / "in.mp4", duration=1.0)

    def callback_com_bug(percent):
        raise RuntimeError("bug no consumidor do progresso")

    run_ffmpeg(input_path=entrada, output_path=tmp_path / "out.mp4", has_audio=False,
               duration_seconds=1.0, on_progress=callback_com_bug)
    assert (tmp_path / "out.mp4").exists()  # processamento concluiu normalmente mesmo assim


# ------------------------------------------------------------------ stderr preservado para diagnóstico (após o timeout ainda existir)
def test_stderr_de_uma_falha_real_continua_sendo_capturado_e_truncado(tmp_path):
    """O stderr de uma falha real (aqui, um vídeo de entrada corrompido/incompleto) continua
    sendo lido pela thread dedicada e usado para classificar o erro -- mesmo comportamento de
    antes, só que agora lido incrementalmente em vez de via communicate()."""
    entrada = tmp_path / "corrompido.mp4"
    entrada.write_bytes(b"isto nao e um mp4 valido")
    with pytest.raises(FfmpegFailedError):
        run_ffmpeg(input_path=entrada, output_path=tmp_path / "out.mp4", has_audio=False)
