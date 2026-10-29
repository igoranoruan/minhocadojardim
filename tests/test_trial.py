"""ETAPA B7 (teste VIP de 24h por link de campanha, pedido e aprovação do Igor, 29/10/2026):
grant_trial_entitlement (services/entitlements.py) + travas de abuso e orquestração do resgate
(services/trial.py).

Mesmo padrão de banco real via migrations de tests/test_entitlements.py (helpers_usage.NOW/DIA)."""
from datetime import timedelta

import pytest
from sqlalchemy import select

import services.trial as trial_service
from config import get_settings
from database.models import Entitlement, TrialRedemption
from helpers_usage import DIA, NOW, give, raw_entitlement
from services.entitlements import TrialAlreadyGrantedError, grant_trial_entitlement
from services.trial import (
    InvalidCampaignCodeError,
    TrialNotEligibleError,
    TrialRedeemRateLimitedError,
    redeem_trial,
)
from utils.security import hash_device_id, hash_ip


@pytest.fixture(autouse=True)
def _limpa_rate_limit_em_memoria():
    """O rate limit de services/trial.py é em memória (mesmo padrão de
    services/pinterest_board.py) -- isolado entre testes para um não vazar tentativas pro outro."""
    trial_service._attempts_by_user.clear()
    yield
    trial_service._attempts_by_user.clear()


def _device(n: int = 1) -> str:
    return f"device-{n:06d}"


# ============================================================================ grant_trial_entitlement (services/entitlements.py)
def test_concede_24h_sem_payment(factory, session):
    usuario = factory.user()
    acesso = grant_trial_entitlement(session, user_id=usuario.id, now=NOW)
    session.refresh(acesso)
    assert acesso.plan_code == "trial_vip"
    assert acesso.payment_id is None
    assert acesso.duration_days == 1
    assert (acesso.starts_at, acesso.expires_at) == (NOW, NOW + DIA)
    assert acesso.status == "granted"


def test_recusa_segunda_tentativa_na_mesma_conta(factory, session):
    """Trava pedida pelo Igor: "segunda tentativa na mesma conta"."""
    usuario = factory.user()
    grant_trial_entitlement(session, user_id=usuario.id, now=NOW)
    with pytest.raises(TrialAlreadyGrantedError):
        grant_trial_entitlement(session, user_id=usuario.id, now=NOW + DIA)
    assert len(_todos(session, usuario)) == 1


def test_recusa_conta_que_ja_pagou_alguma_vez(factory, session):
    """Trava pedida pelo Igor: "conta que já pagou alguma vez também não pode" -- mesmo com o
    plano pago já EXPIRADO (não só vigente)."""
    usuario = factory.user()
    give(factory, session, usuario, "weekly", now=NOW - 30 * DIA)  # já expirado há muito tempo
    with pytest.raises(TrialAlreadyGrantedError):
        grant_trial_entitlement(session, user_id=usuario.id, now=NOW)


def test_recusa_conta_com_varios_entitlements_sem_erro_500(factory, session):
    """Regressão (produção, 29/10/2026): conta com 2+ entitlements (comprou mais de uma vez)
    estourava MultipleResultsFound -> 500 em POST /api/trial/redeem, em vez da recusa amigável."""
    usuario = factory.user()
    give(factory, session, usuario, "weekly", now=NOW - 60 * DIA)
    give(factory, session, usuario, "monthly", now=NOW - 30 * DIA)
    with pytest.raises(TrialAlreadyGrantedError):
        grant_trial_entitlement(session, user_id=usuario.id, now=NOW)
    with pytest.raises(TrialNotEligibleError):
        redeem_trial(session, user=usuario, campaign="ig-teste", device_id=_device(), ip="203.0.113.10", now=NOW)


def test_recusa_se_ja_tem_plano_pago_vigente_nao_empilha(factory, session):
    """Trava pedida pelo Igor: "não deve empilhar com plano pago" (mensagem amigável, nunca
    silenciosamente empilhado depois)."""
    usuario = factory.user()
    give(factory, session, usuario, "monthly", now=NOW)
    with pytest.raises(TrialAlreadyGrantedError):
        grant_trial_entitlement(session, user_id=usuario.id, now=NOW)


def test_recusa_se_tem_plano_pago_futuro_ja_empilhado(factory, session):
    usuario = factory.user()
    raw_entitlement(factory, usuario, plan_code="weekly", starts_at=NOW + 10 * DIA, expires_at=NOW + 17 * DIA)
    with pytest.raises(TrialAlreadyGrantedError):
        grant_trial_entitlement(session, user_id=usuario.id, now=NOW)


def test_recusa_mesmo_com_entitlement_revogado(factory, session):
    usuario = factory.user()
    raw_entitlement(
        factory, usuario, plan_code="weekly", starts_at=NOW - 10 * DIA, expires_at=NOW + 20 * DIA,
        status="revoked", revoked_at=NOW,
    )
    with pytest.raises(TrialAlreadyGrantedError):
        grant_trial_entitlement(session, user_id=usuario.id, now=NOW)


def test_pagar_durante_o_teste_troca_na_hora_sem_mudanca_em_grant_entitlement(factory, session):
    """"O resgate não interfere em planos pagos nem no empilhamento de entitlements" (pedido de
    teste do Igor): grant_entitlement (fluxo de pagamento normal) já sabe encerrar o trial_vip
    vigente na hora e começar o novo plano pago -- SEM nenhuma mudança em grant_entitlement."""
    usuario = factory.user()
    uma_hora = timedelta(hours=1)
    teste = grant_trial_entitlement(session, user_id=usuario.id, now=NOW)
    pago = give(factory, session, usuario, "weekly", now=NOW + uma_hora)  # 1h depois, durante o teste
    session.refresh(teste)
    session.refresh(pago)
    assert teste.expires_at == NOW + uma_hora  # encerrado na hora da troca, não em NOW+24h
    assert (pago.starts_at, pago.expires_at) == (NOW + uma_hora, NOW + uma_hora + 7 * DIA)


def _todos(session, usuario):
    session.expire_all()
    return list(
        session.execute(select(Entitlement).where(Entitlement.user_id == usuario.id)).scalars()
    )


# ============================================================================ redeem_trial (services/trial.py)
def test_resgate_completo_grava_entitlement_e_ledger_com_hash(factory, session):
    usuario = factory.user()
    resultado = redeem_trial(
        session, user=usuario, campaign="ig-teste", device_id=_device(), ip="203.0.113.10", now=NOW,
    )
    assert resultado.expires_at == NOW + DIA

    linha = session.execute(
        select(TrialRedemption).where(TrialRedemption.user_id == usuario.id)
    ).scalar_one()
    assert linha.campaign == "ig-teste"
    assert linha.entitlement_id == resultado.entitlement_id

    segredo = get_settings().ip_hash_secret
    assert linha.device_hash == hash_device_id(_device(), segredo)
    assert linha.ip_hash == hash_ip("203.0.113.10", segredo)
    # IP/aparelho nunca gravados crus -- só o hash (64 hex, formato de HMAC-SHA256).
    assert len(linha.device_hash) == 64 and linha.device_hash != _device()
    assert len(linha.ip_hash) == 64 and linha.ip_hash != "203.0.113.10"


def test_formato_de_campanha_invalido(factory, session):
    usuario = factory.user()
    for ruim in ("", "tem espaço", "a" * 41, "emoji😀", None):
        with pytest.raises(InvalidCampaignCodeError):
            redeem_trial(session, user=usuario, campaign=ruim, device_id=_device(), ip="203.0.113.10", now=NOW)


def test_email_descartavel_e_recusado(factory, session):
    """Trava pedida pelo Igor: "e-mail descartável"."""
    usuario = factory.user(email="alguem@mailinator.com")
    with pytest.raises(TrialNotEligibleError):
        redeem_trial(session, user=usuario, campaign="ig-teste", device_id=_device(), ip="203.0.113.10", now=NOW)


def test_segundo_resgate_no_mesmo_aparelho_e_recusado_mesmo_em_conta_diferente(factory, session):
    """Trava pedida pelo Igor: "mesmo aparelho" -- mesmo quando é outra CONTA tentando (o
    aparelho, não a conta, é o que bloqueia aqui)."""
    aparelho = _device()
    primeira_conta = factory.user()
    redeem_trial(session, user=primeira_conta, campaign="ig-a", device_id=aparelho, ip="203.0.113.10", now=NOW)

    segunda_conta = factory.user()
    with pytest.raises(TrialNotEligibleError):
        redeem_trial(session, user=segunda_conta, campaign="ig-b", device_id=aparelho, ip="198.51.100.20", now=NOW)
    assert _todos(session, segunda_conta) == []


def test_terceiro_resgate_do_mesmo_ip_em_30_dias_e_recusado(factory, session):
    """Trava pedida pelo Igor: "no máximo 2 testes por IP a cada 30 dias"."""
    ip = "203.0.113.55"
    redeem_trial(session, user=factory.user(), campaign="ig-a", device_id=_device(1), ip=ip, now=NOW)
    redeem_trial(session, user=factory.user(), campaign="ig-b", device_id=_device(2), ip=ip, now=NOW + DIA)

    terceira_conta = factory.user()
    with pytest.raises(TrialNotEligibleError):
        redeem_trial(session, user=terceira_conta, campaign="ig-c", device_id=_device(3), ip=ip, now=NOW + 2 * DIA)
    assert _todos(session, terceira_conta) == []


def test_mesmo_ip_apos_30_dias_e_permitido(factory, session):
    ip = "203.0.113.55"
    redeem_trial(session, user=factory.user(), campaign="ig-a", device_id=_device(1), ip=ip, now=NOW)
    redeem_trial(session, user=factory.user(), campaign="ig-b", device_id=_device(2), ip=ip, now=NOW + DIA)

    terceira_conta = factory.user()
    resultado = redeem_trial(
        session, user=terceira_conta, campaign="ig-c", device_id=_device(3), ip=ip, now=NOW + 31 * DIA,
    )
    assert resultado.entitlement_id is not None


def test_conta_que_ja_pagou_e_recusada_pelo_endpoint(factory, session):
    usuario = factory.user()
    give(factory, session, usuario, "monthly", now=NOW - 60 * DIA)
    with pytest.raises(TrialNotEligibleError):
        redeem_trial(session, user=usuario, campaign="ig-teste", device_id=_device(), ip="203.0.113.10", now=NOW)


def test_resgate_de_teste_nao_aciona_recompensa_de_indicacao(factory, session):
    """Spec/pedido do Igor: "o resgate não interfere... em indicações". services/referrals.py
    documenta que process_entitlement_granted só é chamado por payments/gateway.py, nunca por
    services/trial.py/grant_trial_entitlement (estrutural -- redeem_trial nem importa
    services/referrals.py). Aqui provamos o efeito: um indicado que só resgata o teste (nunca
    paga) não rende nenhum dia extra para quem o indicou."""
    from services.referrals import claim_referral, get_or_create_referral_code

    indicador = factory.user()
    get_or_create_referral_code(session, indicador.id)
    indicado = factory.user()
    claim_referral(session, user_id=indicado.id, code=indicador.referral_code)

    redeem_trial(session, user=indicado, campaign="ig-teste", device_id=_device(), ip="203.0.113.10", now=NOW)

    assert _todos(session, indicador) == []  # indicador continua Free: nenhuma recompensa aplicada


def test_rate_limit_da_rota_de_resgate(factory, session):
    from config import TRIAL_REDEEM_RATE_LIMIT_PER_HOUR

    usuario = factory.user()
    # Cada chamada usa um aparelho/campanha diferentes (não queremos testar a trava de aparelho
    # aqui, só o rate limit) -- mas todas do MESMO usuário, que é o rate limit em si.
    for i in range(TRIAL_REDEEM_RATE_LIMIT_PER_HOUR):
        try:
            redeem_trial(session, user=usuario, campaign="ig-teste", device_id=_device(i), ip=f"10.0.0.{i % 250}", now=NOW)
        except TrialNotEligibleError:
            pass  # a conta só tem direito a 1 teste de verdade -- o resto é recusado por OUTRA trava, sem esgotar o rate limit antes da hora
    with pytest.raises(TrialRedeemRateLimitedError):
        redeem_trial(session, user=usuario, campaign="ig-teste", device_id=_device(999), ip="10.0.0.99", now=NOW)
