"""Plano efetivo, limites e saldo: Free por semana (SP), pagos por dia (SP), stacking e isolamento."""
import logging
from datetime import date

import pytest
from sqlalchemy import select

from config import GENERATION_RESERVATION_TTL_SECONDS
from database.base import Base
from database.models import Generation
from helpers_usage import DIA, NOW, consume, give, raw_entitlement, sp
from services.entitlements import EntitlementInconsistencyError
from services.usage import (
    QuotaExceededError,
    can_consume,
    complete_generation,
    fail_generation,
    get_allowance,
    reserve_generation,
)


# ============================================================================ planos e limites
def test_usuario_sem_entitlement_e_free_com_5_por_semana(factory, session):
    a = get_allowance(session, factory.user().id, now=NOW)
    assert (a.plan.code, a.limit, a.used, a.remaining, a.plan.period) == ("free", 5, 0, 5, "week")
    assert a.entitlement_id is None and not a.can_batch
    assert a.period_week == date(2026, 9, 21) and a.period_day == date(2026, 9, 23)


@pytest.mark.parametrize(
    "plano, limite", [("weekly", 10), ("monthly", 20), ("vip_batch", 30)], ids=["semanal-10", "mensal-20", "vip-30"]
)
def test_planos_pagos_limite_por_dia(factory, session, plano, limite):
    usuario = factory.user()
    acesso = give(factory, session, usuario, plano)
    a = get_allowance(session, usuario.id, now=NOW)
    assert (a.plan.code, a.limit, a.remaining, a.plan.period) == (plano, limite, limite, "day")
    assert a.entitlement_id == acesso.id


def test_so_o_vip_pode_usar_lote(factory, session):
    for plano, pode in (("weekly", False), ("monthly", False), ("vip_batch", True)):
        usuario = factory.user()
        give(factory, session, usuario, plano)
        assert get_allowance(session, usuario.id, now=NOW).can_batch is pode


# ============================================================================ validade do entitlement
def test_entitlement_expirado_nao_concede_consumo_volta_ao_free(factory, session):
    usuario = factory.user()
    raw_entitlement(factory, usuario, plan_code="vip_batch", starts_at=NOW - 40 * DIA, expires_at=NOW - 10 * DIA)
    a = get_allowance(session, usuario.id, now=NOW)
    assert (a.plan.code, a.limit) == ("free", 5) and a.entitlement_id is None


def test_entitlement_revogado_nao_concede_consumo(factory, session):
    usuario = factory.user()
    raw_entitlement(
        factory, usuario, plan_code="vip_batch", starts_at=NOW - 1 * DIA, expires_at=NOW + 29 * DIA,
        status="revoked", revoked_at=NOW - DIA / 2,
    )
    assert get_allowance(session, usuario.id, now=NOW).plan.code == "free"


def test_entitlement_futuro_ainda_nao_concede_e_passa_a_valer_no_starts_at(factory, session):
    usuario = factory.user()
    raw_entitlement(factory, usuario, plan_code="monthly", starts_at=NOW + 1 * DIA, expires_at=NOW + 31 * DIA)
    assert get_allowance(session, usuario.id, now=NOW).plan.code == "free"
    assert get_allowance(session, usuario.id, now=NOW + 1 * DIA).plan.code == "monthly"


def test_entitlement_ativo_concede_e_o_fim_do_intervalo_nao(factory, session):
    usuario = factory.user()
    raw_entitlement(factory, usuario, plan_code="weekly", starts_at=NOW, expires_at=NOW + 7 * DIA)
    assert get_allowance(session, usuario.id, now=NOW).plan.code == "weekly"
    assert get_allowance(session, usuario.id, now=NOW + 7 * DIA - DIA / 1000).plan.code == "weekly"
    assert get_allowance(session, usuario.id, now=NOW + 7 * DIA).plan.code == "free"


# ============================================================================ o que conta como consumo
def test_reservada_e_concluida_contam_e_falhada_nao(factory, session):
    usuario = factory.user()
    reservada = reserve_generation(session, user_id=usuario.id, request_id="a", now=NOW)
    concluida = reserve_generation(session, user_id=usuario.id, request_id="b", now=NOW)
    falhada = reserve_generation(session, user_id=usuario.id, request_id="c", now=NOW)
    complete_generation(session, concluida.generation_id, now=NOW)
    fail_generation(session, falhada.generation_id, error_code="download_failed", now=NOW)
    a = get_allowance(session, usuario.id, now=NOW)
    assert (a.used, a.remaining) == (2, 3)
    assert reservada.status == "reserved"


def test_reserva_orfa_antiga_deixa_de_contar_mas_a_concluida_antiga_continua(factory, session):
    usuario = factory.user()
    velha_no_tempo = NOW.replace(hour=9)  # 09:00 (mesmo dia e semana), bem antes do TTL de 35 min em relação a 12:00
    assert (NOW - velha_no_tempo).total_seconds() > GENERATION_RESERVATION_TTL_SECONDS
    orfa = reserve_generation(session, user_id=usuario.id, request_id="orfa", now=velha_no_tempo)
    feita = reserve_generation(session, user_id=usuario.id, request_id="feita", now=velha_no_tempo)
    complete_generation(session, feita.generation_id, now=velha_no_tempo)
    recente = reserve_generation(session, user_id=usuario.id, request_id="recente", now=NOW)
    a = get_allowance(session, usuario.id, now=NOW)
    assert a.used == 2  # a concluída antiga + a recente; a órfã (reservada há > TTL) não conta
    assert (orfa.status, recente.status) == ("reserved", "reserved")


def test_o_ledger_generations_e_a_unica_fonte_do_consumo(factory, session):
    usuario = factory.user()
    factory.generation(
        usuario, plan_code="free", status="completed", period_week=date(2026, 9, 21), period_day=date(2026, 9, 23),
    )
    assert get_allowance(session, usuario.id, now=NOW).used == 1  # linha criada direto no ledger
    tabelas = set(Base.metadata.tables)
    assert not {t for t in tabelas if any(p in t for p in ("counter", "quota", "usage"))}  # sem contadores


# ============================================================================ Free: semana de São Paulo
def test_free_sexta_sabado_e_domingo_pertencem_a_mesma_semana(factory, session):
    usuario = factory.user()
    consume(session, usuario, 5, now=sp(2026, 9, 25, 12))  # sexta
    for momento in (sp(2026, 9, 25, 13), sp(2026, 9, 26, 12), sp(2026, 9, 27, 12), sp(2026, 9, 27, 23, 59, 59)):
        assert get_allowance(session, usuario.id, now=momento).remaining == 0, momento


def test_free_segunda_inicia_nova_semana(factory, session):
    usuario = factory.user()
    consume(session, usuario, 5, now=sp(2026, 9, 25, 12))
    assert get_allowance(session, usuario.id, now=sp(2026, 9, 27, 23, 59, 59)).remaining == 0
    a = get_allowance(session, usuario.id, now=sp(2026, 9, 28, 0, 0, 0))
    assert a.remaining == 5 and a.period_week == date(2026, 9, 28)


def test_free_o_ledger_grava_a_segunda_de_sao_paulo_em_period_week(factory, session):
    usuario = factory.user()
    consume(session, usuario, 1, now=sp(2026, 9, 27, 12))  # domingo
    session.expire_all()
    linha = session.execute(select(Generation)).scalar_one()
    assert linha.period_week == date(2026, 9, 21) and linha.period_day == date(2026, 9, 27)
    assert linha.plan_code == "free" and linha.entitlement_id is None


def test_free_semana_e_de_sao_paulo_nao_de_utc(factory, session):
    usuario = factory.user()
    consume(session, usuario, 5, now=sp(2026, 9, 27, 23, 30))  # domingo 23:30 SP = segunda 02:30 UTC
    assert get_allowance(session, usuario.id, now=sp(2026, 9, 27, 23, 59)).remaining == 0  # ainda a mesma semana
    assert get_allowance(session, usuario.id, now=sp(2026, 9, 28, 0, 1)).remaining == 5


# ============================================================================ pagos: dia de São Paulo
def test_pago_o_limite_diario_reinicia_a_meia_noite_de_sao_paulo(factory, session):
    usuario = factory.user()
    give(factory, session, usuario, "weekly")
    consume(session, usuario, 10, now=NOW)
    assert get_allowance(session, usuario.id, now=sp(2026, 9, 23, 23, 59, 59)).remaining == 0
    assert get_allowance(session, usuario.id, now=sp(2026, 9, 24, 0, 0, 0)).remaining == 10


def test_pago_o_dia_e_o_de_sao_paulo_nao_o_de_utc(factory, session):
    usuario = factory.user()
    give(factory, session, usuario, "weekly")
    consume(session, usuario, 10, now=sp(2026, 9, 23, 22, 0))  # 22:00 SP = 01:00 do dia seguinte em UTC
    assert get_allowance(session, usuario.id, now=sp(2026, 9, 23, 23, 30)).remaining == 0
    assert get_allowance(session, usuario.id, now=sp(2026, 9, 24, 0, 10)).remaining == 10


def test_pago_dia_seguinte_dentro_da_validade_volta_a_ter_o_limite_cheio(factory, session):
    usuario = factory.user()
    give(factory, session, usuario, "monthly")
    consume(session, usuario, 20, now=NOW)
    assert get_allowance(session, usuario.id, now=NOW).remaining == 0
    assert get_allowance(session, usuario.id, now=NOW + 1 * DIA).remaining == 20


# ============================================================================ isolamento Free x pago
def test_uso_pago_nao_gasta_o_saldo_free_da_semana_e_vice_versa(factory, session):
    usuario = factory.user()
    consume(session, usuario, 3, now=NOW - DIA / 24, prefix="free")  # 3 gerações Free (antes do plano)
    raw_entitlement(factory, usuario, plan_code="weekly", starts_at=NOW, expires_at=NOW + DIA / 24)
    pago = get_allowance(session, usuario.id, now=NOW)
    assert (pago.plan.code, pago.used, pago.remaining) == ("weekly", 0, 10)  # o Free não reduz o saldo pago
    consume(session, usuario, 4, now=NOW, prefix="pago")
    assert get_allowance(session, usuario.id, now=NOW).remaining == 6
    depois = get_allowance(session, usuario.id, now=NOW + DIA / 12)  # o plano pago já expirou
    assert (depois.plan.code, depois.used, depois.remaining) == ("free", 3, 2)  # e o uso pago não gastou o Free


# ============================================================================ stacking: limite diário compartilhado
def test_limite_diario_e_compartilhado_entre_acessos_empilhados_no_mesmo_dia(factory, session):
    usuario = factory.user()
    troca = sp(2026, 9, 23, 15, 0)
    raw_entitlement(factory, usuario, plan_code="weekly", starts_at=troca - 7 * DIA, expires_at=troca)
    raw_entitlement(factory, usuario, plan_code="monthly", starts_at=troca, expires_at=troca + 30 * DIA)
    consume(session, usuario, 7, now=sp(2026, 9, 23, 14, 0), prefix="semanal")  # sob o SEMANAL (10/dia)
    a = get_allowance(session, usuario.id, now=sp(2026, 9, 23, 16, 0))  # já sob o MENSAL (20/dia)
    assert (a.plan.code, a.limit, a.used, a.remaining) == ("monthly", 20, 7, 13)  # 13, não 20 e não 10
    consume(session, usuario, 13, now=sp(2026, 9, 23, 16, 0), prefix="mensal")
    with pytest.raises(QuotaExceededError):
        reserve_generation(session, user_id=usuario.id, request_id="a-mais", now=sp(2026, 9, 23, 16, 30))
    assert get_allowance(session, usuario.id, now=sp(2026, 9, 24, 8, 0)).remaining == 20  # dia seguinte


# ============================================================================ inconsistência: nunca decidir em silêncio
def test_sobreposicao_impede_calcular_o_plano_e_nao_cai_no_free(factory, session, caplog):
    usuario = factory.user()
    raw_entitlement(factory, usuario, plan_code="weekly", starts_at=NOW - 1 * DIA, expires_at=NOW + 6 * DIA)
    raw_entitlement(factory, usuario, plan_code="vip_batch", starts_at=NOW - 2 * DIA, expires_at=NOW + 28 * DIA)
    with caplog.at_level(logging.ERROR, logger="minhoca"):
        with pytest.raises(EntitlementInconsistencyError):
            get_allowance(session, usuario.id, now=NOW)
        with pytest.raises(EntitlementInconsistencyError):
            can_consume(session, usuario.id, now=NOW)
    assert "INCONSISTÊNCIA" in caplog.text


def test_can_consume(factory, session):
    usuario = factory.user()
    assert can_consume(session, usuario.id, now=NOW) and can_consume(session, usuario.id, count=5, now=NOW)
    assert not can_consume(session, usuario.id, count=6, now=NOW)
    consume(session, usuario, 5, now=NOW)
    assert not can_consume(session, usuario.id, now=NOW)
