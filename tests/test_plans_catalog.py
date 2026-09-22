"""Catálogo oficial de planos."""
import pytest

from services.plans import (
    FREE,
    FREE_PLAN,
    MONTHLY,
    MONTHLY_PLAN,
    PLANS,
    UnknownPlanError,
    VIP_BATCH,
    VIP_BATCH_PLAN,
    WEEKLY,
    WEEKLY_PLAN,
    get_plan,
    paid_plans,
)


def test_free_5_por_semana_sem_batch():
    assert (FREE_PLAN.code, FREE_PLAN.limit, FREE_PLAN.period) == (FREE, 5, "week")
    assert not FREE_PLAN.paid and FREE_PLAN.duration_days is None and FREE_PLAN.price_cents == 0
    assert not FREE_PLAN.batch_enabled and FREE_PLAN.max_batch_size == 0


def test_semanal_5_por_dia_7_dias_r_9_90_batch_maximo_5():
    assert (WEEKLY_PLAN.code, WEEKLY_PLAN.limit, WEEKLY_PLAN.period) == (WEEKLY, 5, "day")
    assert (WEEKLY_PLAN.duration_days, WEEKLY_PLAN.price_cents, WEEKLY_PLAN.paid) == (7, 990, True)
    assert WEEKLY_PLAN.batch_enabled and WEEKLY_PLAN.max_batch_size == 5


def test_mensal_10_por_dia_30_dias_r_16_90_batch_maximo_10():
    assert (MONTHLY_PLAN.code, MONTHLY_PLAN.limit, MONTHLY_PLAN.period) == (MONTHLY, 10, "day")
    assert (MONTHLY_PLAN.duration_days, MONTHLY_PLAN.price_cents) == (30, 1690)
    assert MONTHLY_PLAN.batch_enabled and MONTHLY_PLAN.max_batch_size == 10


def test_vip_15_por_dia_30_dias_r_29_90_batch_maximo_15():
    assert (VIP_BATCH_PLAN.code, VIP_BATCH_PLAN.limit, VIP_BATCH_PLAN.period) == (VIP_BATCH, 15, "day")
    assert (VIP_BATCH_PLAN.duration_days, VIP_BATCH_PLAN.price_cents) == (30, 2990)
    assert VIP_BATCH_PLAN.batch_enabled and VIP_BATCH_PLAN.max_batch_size == 15


def test_todos_os_planos_pagos_permitem_batch_so_o_free_nao():
    assert {p.code for p in PLANS.values() if p.batch_enabled} == {WEEKLY, MONTHLY, VIP_BATCH}
    assert not FREE_PLAN.batch_enabled


def test_o_teto_de_batch_cresce_com_o_plano():
    assert WEEKLY_PLAN.max_batch_size < MONTHLY_PLAN.max_batch_size < VIP_BATCH_PLAN.max_batch_size


def test_nenhum_plano_e_ilimitado_e_dinheiro_e_sempre_inteiro():
    for plano in PLANS.values():
        assert isinstance(plano.limit, int) and plano.limit > 0
        assert isinstance(plano.price_cents, int) and not isinstance(plano.price_cents, bool)


def test_get_plan_e_paid_plans():
    assert get_plan("weekly") is WEEKLY_PLAN and get_plan("free") is FREE_PLAN
    assert [p.code for p in paid_plans()] == [WEEKLY, MONTHLY, VIP_BATCH]
    with pytest.raises(UnknownPlanError):
        get_plan("premium")


def test_o_plano_e_imutavel():
    with pytest.raises(Exception):
        WEEKLY_PLAN.limit = 99  # dataclass congelada
