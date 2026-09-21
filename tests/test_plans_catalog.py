"""Catálogo oficial de planos."""
import pytest

from config import MAX_BATCH_SIZE
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


def test_free_5_por_semana():
    assert (FREE_PLAN.code, FREE_PLAN.limit, FREE_PLAN.period) == (FREE, 5, "week")
    assert not FREE_PLAN.paid and FREE_PLAN.duration_days is None and FREE_PLAN.price_cents == 0


def test_semanal_10_por_dia_7_dias_r_9_90():
    assert (WEEKLY_PLAN.code, WEEKLY_PLAN.limit, WEEKLY_PLAN.period) == (WEEKLY, 10, "day")
    assert (WEEKLY_PLAN.duration_days, WEEKLY_PLAN.price_cents, WEEKLY_PLAN.paid) == (7, 990, True)


def test_mensal_20_por_dia_30_dias_r_16_90():
    assert (MONTHLY_PLAN.code, MONTHLY_PLAN.limit, MONTHLY_PLAN.period) == (MONTHLY, 20, "day")
    assert (MONTHLY_PLAN.duration_days, MONTHLY_PLAN.price_cents) == (30, 1690)


def test_vip_30_por_dia_30_dias_r_29_90_com_lote_de_ate_10():
    assert (VIP_BATCH_PLAN.code, VIP_BATCH_PLAN.limit, VIP_BATCH_PLAN.period) == (VIP_BATCH, 30, "day")
    assert (VIP_BATCH_PLAN.duration_days, VIP_BATCH_PLAN.price_cents) == (30, 2990)
    assert VIP_BATCH_PLAN.batch_enabled and VIP_BATCH_PLAN.max_batch_size == MAX_BATCH_SIZE == 10


def test_somente_o_vip_permite_lote():
    assert [p.code for p in PLANS.values() if p.batch_enabled] == [VIP_BATCH]
    for plano in (FREE_PLAN, WEEKLY_PLAN, MONTHLY_PLAN):
        assert not plano.batch_enabled and plano.max_batch_size == 0


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
