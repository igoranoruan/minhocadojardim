"""Regras puras da cadeia de entitlements (sem banco): sobreposição, empilhamento e realinhamento.

Trabalham com qualquer objeto que tenha starts_at, expires_at (datetimes com fuso), duration_days
e id. Os intervalos são [starts_at, expires_at): no instante exato de expires_at o acesso acaba e o
seguinte pode começar (sem sobreposição).
"""
from datetime import datetime, timedelta


def _order(item) -> tuple:
    return (item.starts_at, getattr(item, "id", None) or 0)


def is_current(item, now: datetime) -> bool:
    return item.starts_at <= now < item.expires_at


def find_overlaps(items) -> list[tuple]:
    """Pares (a, b) que se sobrepõem. Ordenado por início, basta comparar vizinhos: se existe
    alguma sobreposição, existe uma entre vizinhos."""
    ordered = sorted(items, key=_order)
    return [(a, b) for a, b in zip(ordered, ordered[1:]) if b.starts_at < a.expires_at]


def overlaps_involving_current(items, now: datetime) -> list[tuple]:
    """Sobreposições que envolvem um acesso vigente agora (essas impedem decidir o plano)."""
    return [(a, b) for a, b in find_overlaps(items) if is_current(a, now) or is_current(b, now)]


def next_window(items, now: datetime, duration_days: int) -> tuple[datetime, datetime]:
    """Janela de um novo acesso: começa agora, ou quando termina o último acesso da cadeia.
    Não perde dias comprados e não sobrepõe."""
    start = max([now, *(item.expires_at for item in items)])
    return start, start + timedelta(days=duration_days)


def realign_future(items, now: datetime) -> list[tuple]:
    """Realinha SÓ os acessos futuros (starts_at > now), mantendo duration_days e a ordem.

    Os que já começaram (inclusive o vigente) nunca são movidos. `items` deve conter apenas
    acessos concedidos e não expirados (os revogados não participam). Devolve
    [(item, novo_inicio, novo_fim)] apenas para os que realmente mudam.
    """
    started = [item for item in items if item.starts_at <= now]
    future = sorted((item for item in items if item.starts_at > now), key=_order)
    cursor = max([now, *(item.expires_at for item in started)])
    changes = []
    for item in future:
        new_start = cursor
        new_end = new_start + timedelta(days=item.duration_days)
        if (item.starts_at, item.expires_at) != (new_start, new_end):
            changes.append((item, new_start, new_end))
        cursor = new_end
    return changes
