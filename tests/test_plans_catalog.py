"""Catálogo oficial de planos."""

import pytest

from services.plans import (
    FREE,
    FREE_PLAN,
    MONTHLY,
    MONTHLY_PLAN,
    PLANS,
    TRIAL_VIP,
    TRIAL_VIP_PLAN,
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
    assert not FREE_PLAN.paid
    assert FREE_PLAN.duration_days is None
    assert FREE_PLAN.price_cents == 0
    assert not FREE_PLAN.batch_enabled
    assert FREE_PLAN.max_batch_size == 0
    assert FREE_PLAN.batch_limit == 0
    assert FREE_PLAN.batch_period == "week"


def test_semanal_7_por_dia_7_dias_r_14_90_batch_3_operacoes_semana_maximo_7():
    assert (WEEKLY_PLAN.code, WEEKLY_PLAN.limit, WEEKLY_PLAN.period) == (
        WEEKLY,
        7,
        "day",
    )
    assert (
        WEEKLY_PLAN.duration_days,
        WEEKLY_PLAN.price_cents,
        WEEKLY_PLAN.paid,
    ) == (7, 1490, True)
    assert WEEKLY_PLAN.batch_enabled
    assert WEEKLY_PLAN.max_batch_size == 7
    assert WEEKLY_PLAN.batch_limit == 3
    assert WEEKLY_PLAN.batch_period == "week"


def test_mensal_10_por_dia_30_dias_r_27_90_batch_5_operacoes_semana_maximo_10():
    assert (MONTHLY_PLAN.code, MONTHLY_PLAN.limit, MONTHLY_PLAN.period) == (
        MONTHLY,
        10,
        "day",
    )
    assert (MONTHLY_PLAN.duration_days, MONTHLY_PLAN.price_cents) == (30, 2790)
    assert MONTHLY_PLAN.batch_enabled
    assert MONTHLY_PLAN.max_batch_size == 10
    assert MONTHLY_PLAN.batch_limit == 5
    assert MONTHLY_PLAN.batch_period == "week"


def test_vip_15_por_dia_30_dias_r_47_90_batch_ilimitado_maximo_15():
    assert (VIP_BATCH_PLAN.code, VIP_BATCH_PLAN.limit, VIP_BATCH_PLAN.period) == (
        VIP_BATCH,
        15,
        "day",
    )
    assert (VIP_BATCH_PLAN.duration_days, VIP_BATCH_PLAN.price_cents) == (30, 4790)
    assert VIP_BATCH_PLAN.batch_enabled
    assert VIP_BATCH_PLAN.max_batch_size == 15
    assert VIP_BATCH_PLAN.batch_limit is None
    assert VIP_BATCH_PLAN.batch_period == "week"


def test_todos_os_planos_pagos_permitem_batch_so_o_free_nao():
    # ETAPA B7 (29/10/2026): trial_vip TAMBÉM permite lote (1 operação de até 10 vídeos, ver
    # test_plano_trial_vip_24h abaixo) -- entra neste conjunto de propósito, só o Free fica fora.
    assert {p.code for p in PLANS.values() if p.batch_enabled} == {
        WEEKLY,
        MONTHLY,
        VIP_BATCH,
        TRIAL_VIP,
    }
    assert not FREE_PLAN.batch_enabled


def test_limites_de_batch_por_plano_seguem_a_regra_comercial():
    assert WEEKLY_PLAN.max_batch_size == 7
    assert MONTHLY_PLAN.max_batch_size == 10
    assert VIP_BATCH_PLAN.max_batch_size == 15

    assert WEEKLY_PLAN.batch_limit == 3
    assert MONTHLY_PLAN.batch_limit == 5
    assert VIP_BATCH_PLAN.batch_limit is None


def test_nenhum_plano_tem_cota_de_geracoes_ilimitada_e_dinheiro_e_sempre_inteiro():
    for plano in PLANS.values():
        assert isinstance(plano.limit, int)
        assert plano.limit > 0
        assert isinstance(plano.price_cents, int)
        assert not isinstance(plano.price_cents, bool)


def test_get_plan_e_paid_plans():
    assert get_plan("weekly") is WEEKLY_PLAN
    assert get_plan("free") is FREE_PLAN
    assert get_plan("trial_vip") is TRIAL_VIP_PLAN
    # paid_plans() filtra por `purchasable`, não por `paid` (ETAPA B7) -- trial_vip é paid=True
    # mas purchasable=False, então o resultado não muda.
    assert [p.code for p in paid_plans()] == [WEEKLY, MONTHLY, VIP_BATCH]

    with pytest.raises(UnknownPlanError):
        get_plan("premium")


def test_o_plano_e_imutavel():
    with pytest.raises(Exception):
        WEEKLY_PLAN.limit = 99  # dataclass congelada


# ------------------------------------------------------------------- ETAPA B7 (teste VIP de 24h)
def test_plano_trial_vip_24h():
    """pedido/aprovação do Igor, 29/10/2026: 10 vídeos, 1 lote de até 10, 24h, não vendável."""
    assert (TRIAL_VIP_PLAN.code, TRIAL_VIP_PLAN.limit, TRIAL_VIP_PLAN.period) == (TRIAL_VIP, 10, "trial")
    assert (TRIAL_VIP_PLAN.duration_days, TRIAL_VIP_PLAN.price_cents) == (1, 0)
    assert TRIAL_VIP_PLAN.batch_enabled
    assert TRIAL_VIP_PLAN.max_batch_size == 10
    assert TRIAL_VIP_PLAN.batch_limit == 1
    assert TRIAL_VIP_PLAN.batch_period == "trial"


def test_trial_vip_tem_acesso_mas_nao_e_vendavel():
    """A distinção pedida pelo Igor (29/10/2026, condição 1 da aprovação): trial_vip precisa dar
    ACESSO de verdade (paid=True -- é isso que libera lote/áudio/atalho pelos MESMOS pontos de
    checagem dos planos comprados) mas NUNCA pode ser tratado como venda/cliente pagante
    (purchasable=False -- fora de paid_plans(), e payments/service.py::create_payment recusa
    comprá-lo, ver tests/test_payments_service.py)."""
    assert TRIAL_VIP_PLAN.paid is True
    assert TRIAL_VIP_PLAN.purchasable is False
    assert TRIAL_VIP not in {p.code for p in paid_plans()}

    # Free também nunca foi vendável -- explícito desde esta etapa (antes, só `paid=False` já
    # bastava para o create_payment recusar; agora que ele checa `purchasable`, Free precisa
    # continuar com purchasable=False por conta própria).
    assert FREE_PLAN.purchasable is False

    # Os 3 planos comprados de hoje continuam purchasable=True, sem mudança nenhuma.
    assert WEEKLY_PLAN.purchasable is True
    assert MONTHLY_PLAN.purchasable is True
    assert VIP_BATCH_PLAN.purchasable is True