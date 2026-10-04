"""Programa de indicação: recompensa individual (7 dias) e marco a cada 3 indicações (30 dias de
VIP Batch, aprovação do CÉREBRO, 04/10/2026) -- services/referrals.py + a função nova
services.entitlements.grant_referral_milestone."""
from sqlalchemy import select

from database.models import Referral
from helpers_usage import DIA, NOW, give
from services.entitlements import get_current_entitlement
from services.referrals import (
    claim_referral,
    get_last_applied_reward,
    process_entitlement_granted,
)


def _referral_do(session, referrer, referred):
    return session.execute(
        select(Referral).where(
            Referral.referrer_user_id == referrer.id, Referral.referred_user_id == referred.id
        )
    ).scalar_one()


def _indicar_e_pagar(factory, session, referrer, plano="weekly", now=NOW):
    """Cria um novo usuário indicado por `referrer`, concede-lhe `plano` e processa a recompensa de
    indicação -- mesma sequência real de payments/gateway.py (grant_entitlement seguido de
    process_entitlement_granted)."""
    indicado = factory.user()
    claim_referral(session, user_id=indicado.id, code=referrer.referral_code or _gerar_codigo(session, referrer))
    entitlement = give(factory, session, indicado, plano, now=now)
    process_entitlement_granted(session, user_id=indicado.id, entitlement=entitlement, now=now)
    return indicado


def _gerar_codigo(session, user):
    from services.referrals import get_or_create_referral_code

    return get_or_create_referral_code(session, user.id)


# ============================================================================ recompensa individual (sem marco)
def test_indicacao_unica_da_7_dias_sem_mudar_o_plano(factory, session):
    referrer = factory.user()
    give(factory, session, referrer, "weekly", now=NOW)

    _indicar_e_pagar(factory, session, referrer, now=NOW)

    atual = get_current_entitlement(session, referrer.id, NOW)
    assert atual.plan_code == "weekly"  # marco NÃO bateu ainda (1ª indicação) -- plano intacto
    assert atual.expires_at == NOW + 7 * DIA + 7 * DIA  # 7 (plano) + 7 (recompensa)


def test_duas_indicacoes_dao_7_dias_cada_sem_marco(factory, session):
    referrer = factory.user()
    give(factory, session, referrer, "monthly", now=NOW)

    _indicar_e_pagar(factory, session, referrer, now=NOW)
    _indicar_e_pagar(factory, session, referrer, now=NOW)

    atual = get_current_entitlement(session, referrer.id, NOW)
    assert atual.plan_code == "monthly"  # ainda sem marco (só 2 indicações)
    assert atual.expires_at == NOW + 30 * DIA + 7 * DIA + 7 * DIA


# ============================================================================ marco (3ª indicação)
def test_terceira_indicacao_vira_marco_30_dias_de_vip_batch(factory, session):
    """Caso principal pedido pelo Igor: ao completar a 3ª indicação com o MESMO entitlement vigente
    desde a 1ª, os 7+7 já aplicados são desfeitos e o resultado final é +30 dias de VIP Batch (não
    21, não 30+21) -- e o plano vira vip_batch mesmo o indicador tendo começado no Semanal."""
    referrer = factory.user()
    give(factory, session, referrer, "weekly", now=NOW)  # expira em NOW + 7 dias

    _indicar_e_pagar(factory, session, referrer, now=NOW)  # +7 -> NOW+14
    _indicar_e_pagar(factory, session, referrer, now=NOW)  # +7 -> NOW+21
    _indicar_e_pagar(factory, session, referrer, now=NOW)  # marco: -14 +30 -> NOW+7+30 = NOW+37

    atual = get_current_entitlement(session, referrer.id, NOW)
    assert atual.plan_code == "vip_batch"
    assert atual.expires_at == NOW + 7 * DIA + 30 * DIA  # NUNCA 21 dias, NUNCA 51 (30 em cima de 21)
    assert atual.duration_days == 30  # duração do plano vip_batch, não mais a do weekly original


def test_marco_registra_30_dias_no_ledger_da_terceira_indicacao(factory, session):
    referrer = factory.user()
    give(factory, session, referrer, "weekly", now=NOW)
    indicados = [_indicar_e_pagar(factory, session, referrer, now=NOW) for _ in range(3)]

    linhas = [_referral_do(session, referrer, i) for i in indicados]
    assert [r.reward_days for r in linhas] == [7, 7, 30]
    assert all(r.status == "applied" for r in linhas)

    ultimo = get_last_applied_reward(session, referrer.id)
    assert ultimo.id == linhas[-1].id
    assert ultimo.reward_days == 30


def test_segundo_ciclo_de_marco_tambem_funciona_6a_indicacao(factory, session):
    """O marco se repete a cada novo ciclo de 3 (resposta do Igor: "repete a cada 3")."""
    referrer = factory.user()
    give(factory, session, referrer, "weekly", now=NOW)
    for _ in range(3):
        _indicar_e_pagar(factory, session, referrer, now=NOW)

    depois_do_1_marco = get_current_entitlement(session, referrer.id, NOW)
    fim_apos_1_marco = depois_do_1_marco.expires_at
    assert depois_do_1_marco.plan_code == "vip_batch"

    # 4ª e 5ª: recompensa normal de +7 cada, sobre o entitlement já em VIP Batch.
    _indicar_e_pagar(factory, session, referrer, now=NOW)
    _indicar_e_pagar(factory, session, referrer, now=NOW)
    intermediario = get_current_entitlement(session, referrer.id, NOW)
    assert intermediario.expires_at == fim_apos_1_marco + 7 * DIA + 7 * DIA

    # 6ª: completa outro ciclo -- desfaz os +7+7 do 2º ciclo e aplica +30 de novo (plano já é VIP).
    _indicar_e_pagar(factory, session, referrer, now=NOW)
    final = get_current_entitlement(session, referrer.id, NOW)
    assert final.plan_code == "vip_batch"
    assert final.expires_at == fim_apos_1_marco + 30 * DIA


# ============================================================================ marco com indicador sem plano vigente
def test_marco_com_indicador_free_fica_pendente_e_aplica_tudo_junto_na_proxima_compra(factory, session):
    """Indicador sem plano vigente em nenhuma das 3 indicações: as 3 ficam pending_plan: na
    próxima vez que ELE comprar algo, as 2 primeiras (+7 cada) e a 3ª (marco, 30 dias + upgrade)
    são aplicadas juntas -- como nenhuma delas tinha sido aplicada a entitlement algum ainda, o
    marco aplica como bônus direto (reverse_days=0), sem precisar desfazer nada."""
    referrer = factory.user()  # Free -- nenhum plano vigente

    for _ in range(3):
        _indicar_e_pagar(factory, session, referrer, now=NOW)

    assert get_current_entitlement(session, referrer.id, NOW) is None  # ainda Free
    linhas = session.execute(
        select(Referral).where(Referral.referrer_user_id == referrer.id)
    ).scalars().all()
    assert all(r.status == "pending_plan" for r in linhas)
    assert sorted(r.reward_days for r in linhas) == [7, 7, 30]

    # Agora o indicador finalmente compra um plano (Semanal) -- as 3 pendências aplicam juntas.
    base = give(factory, session, referrer, "weekly", now=NOW)
    process_entitlement_granted(session, user_id=referrer.id, entitlement=base, now=NOW)

    atual = get_current_entitlement(session, referrer.id, NOW)
    assert atual.plan_code == "vip_batch"  # upgrade aplicado pela pendência de marco
    # 7 (plano weekly comprado) + 7 + 7 (normais) + 30 (marco) -- nada foi desfeito pq nada tinha
    # sido aplicado a um entitlement antes (todas estavam pending_plan).
    assert atual.expires_at == NOW + 7 * DIA + 7 * DIA + 7 * DIA + 30 * DIA


# ============================================================================ marco com histórico "sujo" (degrade seguro)
def test_marco_com_historico_sujo_aplica_por_cima_sem_desfazer(factory, session):
    """Se as 2 indicações anteriores do ciclo NÃO foram aplicadas ao MESMO entitlement vigente (aqui:
    a 1ª foi aplicada enquanto o indicador tinha o Semanal, que foi trocado por um Mensal antes da
    2ª/3ª indicação -- troca imediata, services/entitlements.py::grant_entitlement), a função de
    reversão não tenta adivinhar -- aplica o marco por cima do entitlement atual, sem desfazer nada.
    (`now` avança em cada etapa só para não cair no caso-limite, já coberto em
    test_entitlements.py, de duas chamadas de grant_entitlement no MESMO instante.)"""
    referrer = factory.user()
    give(factory, session, referrer, "weekly", now=NOW)
    _indicar_e_pagar(factory, session, referrer, now=NOW)  # aplica +7 no entitlement do weekly

    # Troca de plano NO MEIO do ciclo (2 dias depois) -- cria um entitlement NOVO e diferente.
    novo = give(factory, session, referrer, "monthly", now=NOW + 2 * DIA)
    process_entitlement_granted(session, user_id=referrer.id, entitlement=novo, now=NOW + 2 * DIA)  # pendências, se houver (nenhuma aqui)

    _indicar_e_pagar(factory, session, referrer, now=NOW + 2 * DIA)  # 2ª: +7 no entitlement NOVO (monthly)
    _indicar_e_pagar(factory, session, referrer, now=NOW + 2 * DIA)  # 3ª: marco, mas histórico não é "limpo"

    atual = get_current_entitlement(session, referrer.id, NOW + 2 * DIA)
    assert atual.plan_code == "vip_batch"  # marco ainda faz upgrade de plano
    # monthly (30, a partir de NOW+2d) + 7 (2ª indicação, aplicada a este mesmo entitlement) + 30
    # (marco, por cima, sem desfazer nada porque a 1ª indicação foi aplicada a um entitlement
    # DIFERENTE, o Semanal já encerrado na troca)
    assert atual.expires_at == NOW + 2 * DIA + 30 * DIA + 7 * DIA + 30 * DIA
