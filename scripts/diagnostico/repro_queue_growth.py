"""Reprodução isolada (sem SQLAlchemy/FastAPI -- não disponíveis neste sandbox) da hipótese
arquitetural: o par queue.Queue (ilimitado) + thread daemon de routes/generations.py
(_run_batch_in_background / _stream_batch) cresce sem limite em RAM quando o consumidor do SSE
para de drenar a fila (cliente desconectado/app em background/proxy derrubou a conexão), mas a
thread em background CONTINUA processando o lote inteiro até o fim.

Isto replica EXATAMENTE a forma dos dados reais (mesmos nomes de campo, mesmo padrão
event_queue.put((tipo, dict)) por tick de progresso) e o número de ticks de progresso por item
observado em processor/ffmpeg.py (-progress do FFmpeg emite ~2/seg.) e em
services/generation_flow.py/_run_batch_in_background (on_item_progress chamado 1x para "download"
e Nx para "processing", N proporcional à duração do encode).

Dois cenários simulados lado a lado, 49 itens (tamanho real do lote do incidente), cada item com
~40 ticks de progresso de processamento (equivalente a um encode de ~20s a 2 ticks/seg -- ordem de
grandeza plausível para um vídeo de poucos MB, igual aos tamanhos 1-8MB dos logs do incidente):

  A) "consumidor ativo"   -- cada evento é imediatamente retirado da fila (como um cliente SSE
     saudável faria) -- replica o caminho feliz.
  B) "consumidor parado"  -- a fila nunca é drenada (replica um cliente que desconectou, mas cuja
     desconexão a thread de produção nunca percebe -- não há nenhum sinal de cancelamento
     propagado para dentro de _run_batch_in_background hoje) -- toda a produção de eventos do
     lote inteiro fica acumulada em memória até o lote terminar (ou o processo morrer antes
     disso).
"""
import gc
import queue
import threading
import time

import psutil

PROC = psutil.Process()
TOTAL_ITEMS = 49
TICKS_POR_ITEM = 40  # processing ticks; + 1 evento de status de download + 1 item_complete + 1 batch_progress


def rss_mb() -> float:
    return PROC.memory_info().rss / (1024 * 1024)


def produzir(event_queue: "queue.Queue[tuple[str, dict] | None]") -> None:
    """Réplica fiel do corpo de _run_batch_in_background/on_item_progress/run_batch loop --
    mesma forma de payload, mesma contagem de puts por item, só sem download/ffmpeg reais."""
    concluidos = 0
    for position in range(1, TOTAL_ITEMS + 1):
        generation_id = 1000 + position
        # 1 evento de status "download" (mesmo payload de _run_batch_in_background.on_item_progress)
        event_queue.put(("item_status", {
            "position": position, "generation_id": generation_id,
            "stage": "download", "percent": None, "message": "Baixando...",
        }))
        # N eventos de progresso "processing" (mesmo payload, 1 por tick do -progress do FFmpeg)
        for tick in range(TICKS_POR_ITEM):
            percent = round(100 * tick / TICKS_POR_ITEM, 1)
            event_queue.put(("item_progress", {
                "position": position, "generation_id": generation_id,
                "stage": "processing", "percent": percent, "message": "Processando e limpando...",
            }))
        concluidos += 1
        # payload real de item_complete (campos de BatchItemOutcome.outcome, GenerationOutcome)
        event_queue.put(("item_complete", {
            "position": position, "generation_id": generation_id, "status": "completed",
            "platform": "instagram", "size_bytes": 4_500_000, "duration_seconds": 12.3,
            "output_sha256": "a" * 64, "filename": f"klango-{position}.mp4",
        }))
        event_queue.put(("batch_progress", {"completed": concluidos, "failed": 0, "total": TOTAL_ITEMS}))
    event_queue.put(("complete", {"batch_id": 273, "item_count": TOTAL_ITEMS, "completed": concluidos, "failed": 0}))
    event_queue.put(None)


def cenario_consumidor_ativo() -> None:
    print("\n=== Cenário A: consumidor SSE ATIVO (drena a fila normalmente) ===")
    event_queue: "queue.Queue[tuple[str, dict] | None]" = queue.Queue()
    gc.collect()
    antes = rss_mb()
    print(f"RSS antes do lote: {antes:.2f} MB")

    thread = threading.Thread(target=produzir, args=(event_queue,), daemon=True)
    thread.start()
    recebidos = 0
    amostras = []
    while True:
        item = event_queue.get()
        if item is None:
            break
        recebidos += 1
        if recebidos % (TICKS_POR_ITEM + 3) == 0:  # a cada item concluído
            amostras.append(rss_mb())
    thread.join()
    gc.collect()
    depois = rss_mb()
    print(f"Eventos recebidos: {recebidos}")
    print(f"RSS por item (amostra, primeiros/últimos 5): {amostras[:5]} ... {amostras[-5:]}")
    print(f"RSS depois do lote: {depois:.2f} MB  (delta = {depois - antes:+.2f} MB)")
    print(f"qsize final da fila: {event_queue.qsize()}  (deve ser 0 -- tudo foi consumido)")


def cenario_consumidor_parado() -> None:
    print("\n=== Cenário B: consumidor SSE PARADO (cliente 'desconectou', thread continua) ===")
    event_queue: "queue.Queue[tuple[str, dict] | None]" = queue.Queue()
    gc.collect()
    antes = rss_mb()
    print(f"RSS antes do lote: {antes:.2f} MB")

    thread = threading.Thread(target=produzir, args=(event_queue,), daemon=True)
    thread.start()
    # Simula o generator de _stream_batch sendo fechado/abandonado logo no início (ex.: GeneratorExit
    # por desconexão do cliente, ou o ASGI simplesmente parando de chamar __anext__) -- NINGUÉM MAIS
    # CHAMA event_queue.get() a partir daqui. A thread de produção não tem nenhuma forma de saber
    # disso (nenhum cancellation token é passado para _run_batch_in_background/run_batch hoje) e
    # continua até processar os 49 itens inteiros.
    time.sleep(0.05)  # dá tempo de "ler" só os 2 primeiros eventos, como um cliente real faria
    event_queue.get()
    event_queue.get()
    thread.join()  # espera o lote inteiro terminar de produzir, exatamente como aconteceria em produção
    gc.collect()
    depois = rss_mb()
    print(f"qsize final da fila (eventos presos para sempre em RAM): {event_queue.qsize()}")
    print(f"RSS depois do lote: {depois:.2f} MB  (delta = {depois - antes:+.2f} MB)")
    print(
        "Observação: {} itens x ({} ticks + 3 eventos fixos) = {} objetos dict presos na fila, "
        "retidos só porque a Queue (e a própria thread) nunca são coletados -- a referência fica "
        "viva em routes/generations.py::_stream_batch enquanto o generator StreamingResponse não "
        "for finalizado pelo ASGI, e a THREAD em si roda até o fim independente disso."
        .format(TOTAL_ITEMS, TICKS_POR_ITEM, event_queue.qsize())
    )


if __name__ == "__main__":
    cenario_consumidor_ativo()
    cenario_consumidor_parado()
