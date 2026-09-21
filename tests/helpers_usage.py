"""Utilitários dos testes de planos, entitlements e uso (não são testes nem fixtures)."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from services.entitlements import grant_entitlement
from services.usage import complete_generation, reserve_generation

SP = ZoneInfo("America/Sao_Paulo")


def sp(y, m, d, h=12, mi=0, s=0) -> datetime:
    return datetime(y, m, d, h, mi, s, tzinfo=SP)


# 2026-09-23 é uma quarta-feira; a semana de São Paulo vai de 21/09 (segunda) a 27/09 (domingo).
NOW = sp(2026, 9, 23, 12)
DIA = timedelta(days=1)


def give(factory, session, user, plan_code, now=NOW):
    """Concede um plano pago pelo serviço (com um pagamento aprovado de apoio)."""
    payment = factory.payment(user, plan_code=plan_code, status="approved")
    return grant_entitlement(session, user_id=user.id, payment_id=payment.id, plan_code=plan_code, now=now)


def raw_entitlement(factory, user, *, plan_code, starts_at, expires_at, status="granted", revoked_at=None, days=None):
    """Cria um entitlement direto no banco (para montar cenários, inclusive inconsistentes)."""
    payment = factory.payment(user, plan_code=plan_code, status="approved")
    dias = days or max(1, round((expires_at - starts_at).total_seconds() / 86400))
    return factory.entitlement(
        payment,
        plan_code=plan_code,
        duration_days=dias,
        starts_at=starts_at,
        expires_at=expires_at,
        status=status,
        revoked_at=revoked_at,
        revoke_reason="refunded" if status == "revoked" else None,
    )


def consume(session, user, n, *, now=NOW, complete=True, prefix="c"):
    """Reserva n gerações (e as conclui, por padrão). Devolve os ids."""
    ids = []
    for i in range(n):
        reserva = reserve_generation(session, user_id=user.id, request_id=f"{prefix}-{i}", now=now)
        if complete:
            complete_generation(session, reserva.generation_id, now=now)
        ids.append(reserva.generation_id)
    return ids


def run_parallel(maker, n, function):
    """Roda `function(session, i)` em n threads (cada uma com a SUA sessão e conexão), largando todas
    juntas. Devolve [('ok', valor) | ('erro', exceção)], na ordem das threads."""
    import threading

    barrier = threading.Barrier(n)
    results = [None] * n

    def worker(i):
        barrier.wait(timeout=30)
        session = maker()
        try:
            results[i] = ("ok", function(session, i))
        except Exception as exc:  # o teste inspeciona o tipo de cada erro
            results[i] = ("erro", exc)
        finally:
            session.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=90)
    assert all(r is not None for r in results), "alguma thread não terminou"
    return results
