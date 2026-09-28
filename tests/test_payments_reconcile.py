"""payments/gateway.py::reconcile_webhook_payment (Etapa 10.3): a reconciliação autoritativa
disparada pelo webhook -- localização do Payment, trava por usuário, transições, e a regra de
grant/revoke que NÃO depende do status local anterior (correção 1). payments.client.get_payment
é sempre mockado -- nenhum teste desta suíte fala com a rede real.
"""
from unittest.mock import patch

from sqlalchemy import select

from database.models import Entitlement
from database.types import utcnow
from payments.gateway import reconcile_webhook_payment
from payments.service import create_payment


def _resposta_get(mp_id, status, external_reference, status_detail=None):
    return {
        "status": 200,
        "response": {
            "id": mp_id,
            "status": status,
            "status_detail": status_detail,
            "external_reference": external_reference,
        },
    }


def _entitlements_do_payment(session, payment_id):
    return session.execute(select(Entitlement).where(Entitlement.payment_id == payment_id)).scalars().all()


# ============================================================================ grant (correção 1)
def test_grant_e_chamado_mesmo_quando_payment_ja_esta_approved(factory, session):
    """A 10.2 pode deixar Payment.status == "approved" sem nunca ter criado entitlement -- o
    webhook deve conceder mesmo assim, sem checar o status local anterior."""
    payment = factory.payment(
        status="approved", mp_payment_id="555", external_reference="ref-approved-sem-entitlement",
        approved_at=utcnow(),
    )
    assert _entitlements_do_payment(session, payment.id) == []

    with patch("payments.client.get_payment", return_value=_resposta_get("555", "approved", payment.external_reference)):
        resultado = reconcile_webhook_payment(session, mp_payment_id="555")

    assert resultado.outcome == "granted"
    entitlements = _entitlements_do_payment(session, payment.id)
    assert len(entitlements) == 1


def test_grant_repetido_nao_duplica_entitlement(factory, session):
    payment = factory.payment(status="pending", external_reference="ref-grant-repetido")

    with patch("payments.client.get_payment", return_value=_resposta_get("777", "approved", payment.external_reference)):
        reconcile_webhook_payment(session, mp_payment_id="777")
        segundo = reconcile_webhook_payment(session, mp_payment_id="777")

    assert segundo.outcome == "granted"
    assert len(_entitlements_do_payment(session, payment.id)) == 1


def test_approved_grava_approved_at_uma_unica_vez(factory, session):
    payment = factory.payment(status="pending", external_reference="ref-approved-at")

    with patch("payments.client.get_payment", return_value=_resposta_get("888", "approved", payment.external_reference)):
        reconcile_webhook_payment(session, mp_payment_id="888")

    session.refresh(payment)
    primeiro_approved_at = payment.approved_at
    assert primeiro_approved_at is not None

    with patch("payments.client.get_payment", return_value=_resposta_get("888", "approved", payment.external_reference)):
        reconcile_webhook_payment(session, mp_payment_id="888")

    session.refresh(payment)
    assert payment.approved_at == primeiro_approved_at


# ============================================================================ revoke (correção 1)
def test_revoke_e_chamado_mesmo_quando_payment_ja_esta_refunded(factory, session):
    payment = factory.payment(status="approved", mp_payment_id="999", external_reference="ref-revoke-repetido")
    factory.entitlement(payment)

    with patch("payments.client.get_payment", return_value=_resposta_get("999", "refunded", payment.external_reference)):
        primeiro = reconcile_webhook_payment(session, mp_payment_id="999")
        segundo = reconcile_webhook_payment(session, mp_payment_id="999")

    assert primeiro.outcome == "revoked"
    assert segundo.outcome == "revoked"  # revoke_entitlement chamado de novo, idempotente
    entitlement = _entitlements_do_payment(session, payment.id)[0]
    assert entitlement.status == "revoked"


def test_refunded_sem_entitlement_e_no_op_mas_atualiza_payment(factory, session):
    """Um reembolso pode chegar sem que o approved correspondente tenha sido processado antes --
    nada a revogar, mas o Payment ainda reflete o status autoritativo."""
    payment = factory.payment(status="pending", external_reference="ref-refund-sem-entitlement")
    assert _entitlements_do_payment(session, payment.id) == []

    with patch("payments.client.get_payment", return_value=_resposta_get("111", "refunded", payment.external_reference)):
        resultado = reconcile_webhook_payment(session, mp_payment_id="111")

    assert resultado.outcome == "no_op"
    session.refresh(payment)
    assert payment.status == "refunded"
    assert _entitlements_do_payment(session, payment.id) == []


def test_charged_back_revoga_entitlement(factory, session):
    payment = factory.payment(status="approved", mp_payment_id="222", external_reference="ref-chargeback")
    factory.entitlement(payment)

    with patch("payments.client.get_payment", return_value=_resposta_get("222", "charged_back", payment.external_reference)):
        resultado = reconcile_webhook_payment(session, mp_payment_id="222")

    assert resultado.outcome == "revoked"
    session.refresh(payment)
    assert payment.status == "charged_back"
    assert payment.refunded_at is not None


def test_refunded_grava_refunded_at_uma_unica_vez(factory, session):
    payment = factory.payment(status="approved", mp_payment_id="333", external_reference="ref-refunded-at")
    factory.entitlement(payment)

    with patch("payments.client.get_payment", return_value=_resposta_get("333", "refunded", payment.external_reference)):
        reconcile_webhook_payment(session, mp_payment_id="333")

    session.refresh(payment)
    primeiro_refunded_at = payment.refunded_at
    assert primeiro_refunded_at is not None

    with patch("payments.client.get_payment", return_value=_resposta_get("333", "refunded", payment.external_reference)):
        reconcile_webhook_payment(session, mp_payment_id="333")

    session.refresh(payment)
    assert payment.refunded_at == primeiro_refunded_at


# ============================================================================ estados sem ação de entitlement
def test_pending_nao_faz_nada(factory, session):
    payment = factory.payment(status="pending", external_reference="ref-pending")

    with patch("payments.client.get_payment", return_value=_resposta_get("444", "pending", payment.external_reference)):
        resultado = reconcile_webhook_payment(session, mp_payment_id="444")

    assert resultado.outcome == "no_op"
    assert _entitlements_do_payment(session, payment.id) == []


def test_rejected_atualiza_status_sem_entitlement(factory, session):
    payment = factory.payment(status="pending", external_reference="ref-rejected")

    with patch("payments.client.get_payment", return_value=_resposta_get("555000", "rejected", payment.external_reference, status_detail="cc_rejected_other_reason")):
        resultado = reconcile_webhook_payment(session, mp_payment_id="555000")

    assert resultado.outcome == "no_op"
    session.refresh(payment)
    assert payment.status == "rejected"
    assert payment.status_detail == "cc_rejected_other_reason"
    assert _entitlements_do_payment(session, payment.id) == []


def test_cancelled_atualiza_status_sem_entitlement(factory, session):
    payment = factory.payment(status="pending", external_reference="ref-cancelled")

    with patch("payments.client.get_payment", return_value=_resposta_get("666000", "cancelled", payment.external_reference)):
        resultado = reconcile_webhook_payment(session, mp_payment_id="666000")

    assert resultado.outcome == "no_op"
    session.refresh(payment)
    assert payment.status == "cancelled"


def test_status_desconhecido_vira_pending(factory, session):
    payment = factory.payment(status="pending", external_reference="ref-in-process")

    with patch("payments.client.get_payment", return_value=_resposta_get("777000", "in_process", payment.external_reference)):
        resultado = reconcile_webhook_payment(session, mp_payment_id="777000")

    assert resultado.outcome == "no_op"
    session.refresh(payment)
    assert payment.status == "pending"


# ============================================================================ orphan / conflict
def test_payment_nao_encontrado_e_orphan(session):
    with patch("payments.client.get_payment", return_value=_resposta_get("999999", "approved", "ref-inexistente")):
        resultado = reconcile_webhook_payment(session, mp_payment_id="999999")

    assert resultado.outcome == "orphan"
    assert resultado.payment is None


def test_mp_payment_id_pertence_a_outro_payment_e_conflict(factory, session):
    """paymentC já tem mp_payment_id="111" gravado. Uma notificação nova (mp_payment_id="222")
    cuja resposta autoritativa devolve external_reference igual ao de paymentC só encontra esse
    Payment pelo caminho de external_reference -- e detecta que o mp_payment_id não bate."""
    payment_c = factory.payment(status="approved", mp_payment_id="111", external_reference="ref-colisao")

    with patch("payments.client.get_payment", return_value=_resposta_get("222", "approved", "ref-colisao")):
        resultado = reconcile_webhook_payment(session, mp_payment_id="222")

    assert resultado.outcome == "conflict"
    session.refresh(payment_c)
    assert payment_c.mp_payment_id == "111"  # intocado
    assert payment_c.status == "approved"


def test_external_reference_nao_bate_e_conflict(factory, session):
    payment_d = factory.payment(status="pending", mp_payment_id="333", external_reference="ref-d")

    with patch("payments.client.get_payment", return_value=_resposta_get("333", "approved", "ref-x-diferente")):
        resultado = reconcile_webhook_payment(session, mp_payment_id="333")

    assert resultado.outcome == "conflict"
    session.refresh(payment_d)
    assert payment_d.status == "pending"  # intocado
    assert _entitlements_do_payment(session, payment_d.id) == []


def test_reversao_refunded_para_approved_e_recusada(factory, session):
    payment = factory.payment(status="refunded", mp_payment_id="444", external_reference="ref-reversao", refunded_at=utcnow())

    with patch("payments.client.get_payment", return_value=_resposta_get("444", "approved", payment.external_reference)):
        resultado = reconcile_webhook_payment(session, mp_payment_id="444")

    assert resultado.outcome == "conflict"
    session.refresh(payment)
    assert payment.status == "refunded"  # intocado
    assert _entitlements_do_payment(session, payment.id) == []


# ============================================================================ vinculação tardia de mp_payment_id
def test_encontra_por_external_reference_quando_mp_payment_id_ainda_nao_gravado(factory, session):
    """Cenário: a 10.2 falhou por PaymentGatewayError e o Payment ficou "pending" sem
    mp_payment_id. O webhook consegue localizar o Payment pelo external_reference e vincula o
    mp_payment_id nessa mesma reconciliação."""
    payment = create_payment(session, user_id=factory.user().id, plan_code="weekly", method="pix")
    assert payment.mp_payment_id is None

    with patch("payments.client.get_payment", return_value=_resposta_get("555555", "approved", payment.external_reference)):
        resultado = reconcile_webhook_payment(session, mp_payment_id="555555")

    assert resultado.outcome == "granted"
    session.refresh(payment)
    assert payment.mp_payment_id == "555555"
