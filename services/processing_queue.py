"""Fila de processamento: no máximo MAX_CONCURRENT_PROCESSING downloads+processamentos ao mesmo
tempo no servidor inteiro (vídeo e áudio somados). Pedido e aprovação do Igor (02/11/2026, item 3
da preparação do teste VIP para ~100 pessoas).

Por quê: cada pedido abria o próprio yt-dlp + FFmpeg numa thread, sem limite nenhum, num servidor
de 1 CPU e 2 GB -- com muita gente ao mesmo tempo, os vídeos que precisam ser reconvertidos
disputariam a CPU (estourando o tempo limite de 3 min) e a memória (risco de o processo cair), e
os pedidos ao Instagram/TikTok sairiam todos de uma vez do mesmo IP do servidor.

Como: um semáforo global (o app roda em 1 instância, 1 processo uvicorn -- mesma premissa dos
rate limits em memória de services/pinterest_board.py). Quem não consegue vaga na hora espera,
avisando a cada `poll_seconds` (o site mostra "Na fila…" e a conexão do stream continua viva).
Depois de `max_wait_seconds` sem vaga, desiste com QueueWaitTimeoutError -- quem chama marca o
item como falho (a cota volta) com uma mensagem de servidor ocupado. O teto de espera existe para
uma reserva nunca expirar enquanto espera (GENERATION_RESERVATION_TTL_SECONDS = 35 min).

Puro threading: sem banco, sem rede, sem download/processor (quem chama faz a ponte).
"""
import logging
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from config import (
    MAX_CONCURRENT_PROCESSING,
    PROCESSING_QUEUE_MAX_WAIT_SECONDS,
    PROCESSING_QUEUE_POLL_SECONDS,
)

logger = logging.getLogger("minhoca")

_slots = threading.BoundedSemaphore(MAX_CONCURRENT_PROCESSING)


class QueueWaitTimeoutError(Exception):
    """Ficou mais de `max_wait_seconds` esperando vaga na fila."""


@contextmanager
def processing_slot(
    *,
    on_wait: Callable[[], None] | None = None,
    max_wait_seconds: float | None = None,
    poll_seconds: float | None = None,
    label: str = "",
) -> Iterator[None]:
    """Ocupa uma vaga durante o bloco `with`. Com vaga livre, entra na hora e `on_wait` nunca é
    chamado. Sem vaga, chama `on_wait()` logo e de novo a cada `poll_seconds` até conseguir, ou
    levanta QueueWaitTimeoutError depois de `max_wait_seconds`. A vaga é sempre devolvida ao sair
    do bloco, com sucesso ou exceção. Uma falha em `on_wait` (ex.: cliente desconectou) nunca
    impede a espera de continuar. Sem valores explícitos, usa os de config.py (lidos na hora da
    chamada, não na importação)."""
    if max_wait_seconds is None:
        max_wait_seconds = PROCESSING_QUEUE_MAX_WAIT_SECONDS
    if poll_seconds is None:
        poll_seconds = PROCESSING_QUEUE_POLL_SECONDS
    if not _slots.acquire(blocking=False):
        inicio = time.monotonic()
        logger.info("[FILA] sem vaga livre, aguardando %s", label)
        while True:
            if on_wait is not None:
                try:
                    on_wait()
                except Exception:
                    logger.warning("[FILA] falha ao avisar que está na fila (segue esperando)", exc_info=True)
            restante = max_wait_seconds - (time.monotonic() - inicio)
            if restante <= 0:
                logger.warning("[FILA] desistiu depois de %.0fs sem vaga %s", max_wait_seconds, label)
                raise QueueWaitTimeoutError()
            if _slots.acquire(timeout=min(poll_seconds, restante)):
                logger.info("[FILA] entrou depois de %.0fs %s", time.monotonic() - inicio, label)
                break
    try:
        yield
    finally:
        _slots.release()
