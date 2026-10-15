"""DIAGNÓSTICO TEMPORÁRIO (Etapa 1) -- mede o PICO de RSS real (processo Python + todos os
processos filhos, recursivo -- pega o ffmpeg que o yt-dlp invoca internamente pra merge, e
qualquer ffmpeg que processor/service.py roda depois) ao baixar e processar UMA URL de cada vez
pelo caminho de produção de verdade (download.service.download_video -> processor.service
.process_video), SEM NENHUM MOCK.

Isso substitui o teste mockado que eu tinha proposto antes: os números do Render mostram que não é
acúmulo entre itens (93 -> 106 -> 130 -> 139 MB, devagar) e sim um PICO de um item só, de ~140MB
pra >512MB em ~30s, terminando num OOM kill 31s depois do último "[RESULT_STORAGE] salvo" -- então
o mock (que nunca chama yt-dlp/ffmpeg de verdade) não ia capturar isso de jeito nenhum.

NÃO precisa de banco/sqlalchemy/pytest -- só o venv do projeto (yt-dlp, ffmpeg, as dependências de
requirements.txt) e internet de verdade pra baixar do Instagram. Roda a partir da RAIZ do projeto.

Como rodar:
    python diag_pico_memoria.py "https://www.instagram.com/reel/DdoVDZMMOkk/" "<url normal 1>" "<url normal 2>"

A primeira URL deve ser a que estava em processamento na hora da queda (pela UI); as demais,
URLs "normais" do mesmo lote (ou de teste), pra comparação.

Saída por URL: pico de RSS total (self + filhos), em que momento (download ou processamento)
aconteceu, se algum processo filho (ffmpeg do yt-dlp pro merge, ou o ffmpeg do processor/) chegou a
existir e qual foi seu pico individual, resolução/bitrate/codec real do arquivo baixado (via
ffprobe direto, só para este diagnóstico) e duração.

Não apaga nada de produção, não precisa de approval prévio -- só lê/baixa/processa e deleta os
arquivos temporários que ele mesmo criou ao final, exatamente como o fluxo real faria.
"""
import json
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import psutil

from config import get_settings
from download.errors import DownloadError
from download.service import download_video
from processor.errors import ProcessorError
from processor.service import process_video

AMOSTRA_INTERVALO_S = 0.2
MIB = 1024 * 1024


@dataclass
class Amostra:
    t: float
    fase: str
    total_mb: float
    por_processo: dict[int, tuple[str, float]]  # pid -> (nome, mb)


@dataclass
class Resultado:
    url: str
    amostras: list[Amostra] = field(default_factory=list)
    erro: str | None = None
    probe_info: dict | None = None
    duracao_download_s: float = 0.0
    duracao_processamento_s: float = 0.0


def _rss_arvore_mb(proc: psutil.Process) -> tuple[float, dict[int, tuple[str, float]]]:
    """RSS do processo + de TODOS os filhos recursivos (pega o ffmpeg que o yt-dlp chama pra
    merge de vídeo+áudio quando o format escolhido vem separado, e o ffmpeg que processor/service
    .py chama em seguida). Processo já morto/zumbi é ignorado (psutil.NoSuchProcess)."""
    total = 0.0
    por_processo: dict[int, tuple[str, float]] = {}
    alvos = [proc] + proc.children(recursive=True)
    for p in alvos:
        try:
            mb = p.memory_info().rss / MIB
            nome = p.name()
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
        total += mb
        por_processo[p.pid] = (nome, mb)
    return total, por_processo


def _amostrador(proc: psutil.Process, fase_atual: list[str], amostras: list[Amostra], parar: threading.Event) -> None:
    inicio = time.monotonic()
    while not parar.is_set():
        total, detalhe = _rss_arvore_mb(proc)
        amostras.append(Amostra(t=time.monotonic() - inicio, fase=fase_atual[0], total_mb=total, por_processo=detalhe))
        time.sleep(AMOSTRA_INTERVALO_S)


def _ffprobe_bruto(path: Path) -> dict:
    """ffprobe direto (não reaproveita processor/probe.py de propósito -- aqui eu quero TODOS os
    campos, incluindo resolução/bitrate/format_name, que ProbeResult não expõe, só pra este
    relatório de diagnóstico)."""
    executable = get_settings().ffprobe_path
    args = [executable, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]
    completed = subprocess.run(args, capture_output=True, timeout=20, check=False)
    return json.loads(completed.stdout.decode("utf-8", errors="replace"))


def _resumo_probe(data: dict) -> dict:
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None) or {}
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None) or {}
    fmt = data.get("format") or {}
    return {
        "video_codec": video.get("codec_name"),
        "largura": video.get("width"),
        "altura": video.get("height"),
        "video_bitrate_kbps": (int(video["bit_rate"]) // 1000) if video.get("bit_rate") else None,
        "audio_codec": audio.get("codec_name"),
        "duracao_s": float(fmt["duration"]) if fmt.get("duration") else None,
        "tamanho_bytes": int(fmt["size"]) if fmt.get("size") else None,
        "format_name": fmt.get("format_name"),
    }


def diagnosticar_url(url: str) -> Resultado:
    resultado = Resultado(url=url)
    proc = psutil.Process()
    fase_atual = ["idle"]
    amostras: list[Amostra] = []
    parar = threading.Event()

    thread = threading.Thread(target=_amostrador, args=(proc, fase_atual, amostras, parar), daemon=True)
    thread.start()

    download_path = None
    output_path = None
    try:
        fase_atual[0] = "download"
        t0 = time.monotonic()
        download_result = download_video(url)
        resultado.duracao_download_s = time.monotonic() - t0
        download_path = download_result.temp_path

        try:
            resultado.probe_info = _resumo_probe(_ffprobe_bruto(download_path))
        except Exception as exc:  # diagnóstico só -- nunca deve interromper a medição principal
            resultado.probe_info = {"erro_probe": str(exc)}

        if download_result.media_type == "video":
            fase_atual[0] = "processamento"
            t1 = time.monotonic()
            processing_result = process_video(download_path)
            resultado.duracao_processamento_s = time.monotonic() - t1
            output_path = processing_result.output_path
        else:
            resultado.erro = f"media_type={download_result.media_type!r} (não é vídeo -- rode separado se quiser medir image/image_zip)"
    except (DownloadError, ProcessorError) as exc:
        resultado.erro = f"{exc.__class__.__name__}: {exc}"
    finally:
        parar.set()
        thread.join()
        resultado.amostras = amostras
        for p in (download_path, output_path):
            if p is not None:
                try:
                    Path(p).unlink(missing_ok=True)
                except OSError:
                    pass

    return resultado


def _imprimir_relatorio(resultado: Resultado) -> None:
    print(f"\n{'=' * 90}\nURL: {resultado.url}")
    if resultado.erro:
        print(f"  ERRO: {resultado.erro}")
    if resultado.probe_info:
        print(f"  ffprobe: {resultado.probe_info}")
    print(f"  duração download: {resultado.duracao_download_s:.1f}s | processamento: {resultado.duracao_processamento_s:.1f}s")

    if not resultado.amostras:
        print("  (nenhuma amostra coletada)")
        return

    pico = max(resultado.amostras, key=lambda a: a.total_mb)
    print(f"  PICO de RSS (processo principal + filhos): {pico.total_mb:.1f} MB, na fase '{pico.fase}', t={pico.t:.1f}s")
    print("  processos ativos no momento do pico:")
    for pid, (nome, mb) in sorted(pico.por_processo.items(), key=lambda kv: -kv[1][1]):
        print(f"    pid={pid:<7} {nome:<20} {mb:8.1f} MB")

    # Pico de cada nome de processo distinto visto ao longo de toda a execução (ex.: se o ffmpeg
    # do merge do yt-dlp e o ffmpeg do processor/ rodaram em momentos diferentes, cada um aparece
    # aqui com seu PRÓPRIO pico individual, não só o momento do pico total).
    picos_por_nome: dict[str, float] = {}
    for amostra in resultado.amostras:
        for _pid, (nome, mb) in amostra.por_processo.items():
            picos_por_nome[nome] = max(picos_por_nome.get(nome, 0.0), mb)
    print("  pico individual por tipo de processo (ao longo de toda a execução):")
    for nome, mb in sorted(picos_por_nome.items(), key=lambda kv: -kv[1]):
        print(f"    {nome:<20} {mb:8.1f} MB")

    rss_inicial = resultado.amostras[0].total_mb
    rss_final = resultado.amostras[-1].total_mb
    print(f"  RSS no início: {rss_inicial:.1f} MB | RSS no final: {rss_final:.1f} MB | pico: {pico.total_mb:.1f} MB")


def main() -> None:
    urls = sys.argv[1:]
    if not urls:
        print(__doc__)
        sys.exit(1)

    resultados = [diagnosticar_url(url) for url in urls]
    for resultado in resultados:
        _imprimir_relatorio(resultado)

    print(f"\n{'=' * 90}\nRESUMO COMPARATIVO")
    for resultado in resultados:
        pico_mb = max((a.total_mb for a in resultado.amostras), default=0.0)
        res = resultado.probe_info or {}
        print(
            f"  {resultado.url[:60]:<60} pico={pico_mb:7.1f} MB  "
            f"{res.get('largura')}x{res.get('altura')}  {res.get('video_bitrate_kbps')}kbps  "
            f"{res.get('duracao_s')}s  erro={resultado.erro}"
        )


if __name__ == "__main__":
    main()
