"""POST /api/webhooks/mercadopago (Etapa 10.3): contrato HTTP completo -- assinatura, PaymentEvent,
localização do Payment, grant/revoke, e as garantias de segurança pedidas explicitamente
("assinatura inválida -> zero escrita nova"; "Payment já approved + webhook approved -> grant
chamado, só um Entitlement permanece"). payments.client.get_payment é sempre mockado.
"""
import hashlib
import hmac
import json
from unittest.mock import patch

from sqlalchemy import select

from database.models import Entitlement, Payment, PaymentEvent
from database.types import utcnow

SECRET = "segredo-webhook-testes"


def _assinar(data_id: str, request_id: str, ts: str, secret: str = SECRET) -> str:
    manifest = f"id:{data_id.lower()};request-id:{request_id};ts:{ts};"
    v1 = hmac.new(secret.encode(), manifest.encode(), hashlib.sha256).hexdigest()
    return f"ts={ts},v1={v1}"


def _headers(data_id: str, request_id: str = "req-1", ts: str = "1700000000", secret: str = SECRET) -> dict:
    return {"x-signature": _assinar(data_id, request_id, ts, secret), "x-request-id": request_id}


def _body(data_id: str, tipo: str = "payment", acao: str = "payment.updated") -> bytes:
    return json.dumps({"type": tipo, "action": acao, "data": {"id": data_id}}).encode()


def _resposta_get(mp_id, status, external_reference, status_detail=None):
    return {
        "status": 200,
        "response": {"id": mp_id, "status": status, "status_detail": status_detail, "external_reference": external_reference},
    }


def _post(auth_client, data_id, *, headers=None, body=None, request_id="req-1", ts="1700000000"):
    return auth_client.post(
        f"/api/webhooks/mercadopago?data.id={data_id}",
        headers=headers if headers is not None else _headers(data_id, request_id=request_id, ts=ts),
        content=body if body is not None else _body(data_id),
    )


def _contagens(session):
    return (
        len(session.execute(select(Payment)).scalars().all()),
        len(session.execute(select(Entitlement)).scalars().all()),
        len(session.execute(select(PaymentEvent)).scalars().all()),
    )


# ============================================================================ assinatura
def test_assinatura_invalida_recebe_401_e_zero_escrita_nova(auth_client, use_settings, factory, session):
    use_settings(mp_webhook_secret=SECRET)
    payment = factory.payment(mp_payment_id="1", external_reference="ref-1")
    antes = _contagens(session)

    resposta = auth_client.post(
        "/api/webhooks/mercadopago?data.id=1",
        headers={"x-signature": "ts=1700000000,v1=" + "0" * 64, "x-request-id": "req-1"},
        content=_body("1"),
    )

    assert resposta.status_code == 401
    depois = _contagens(session)
    assert depois == antes
    session.refresh(payment)
    assert payment.status == "pending"


def test_secret_nao_configurado_recusa_tudo(auth_client, use_settings):
    use_settings(mp_webhook_secret="")
    resposta = _post(auth_client, "1")
    assert resposta.status_code == 401


def test_headers_ausentes_recebem_401(auth_client, use_settings):
    use_settings(mp_webhook_secret=SECRET)
    resposta = auth_client.post("/api/webhooks/mercadopago?data.id=1", content=_body("1"))
    assert resposta.status_code == 401


# ============================================================================ conflito data.id query x corpo
def test_data_id_query_diferente_do_corpo_e_conflict_200(auth_client, use_settings, session):
    use_settings(mp_webhook_secret=SECRET)
    headers = _headers("1")  # assina para data.id=1 (o da query)

    resposta = auth_client.post(
        "/api/webhooks/mercadopago?data.id=1", headers=headers, content=_body("2"),  # corpo diz 2
    )

    assert resposta.status_code == 200
    evento = session.execute(select(PaymentEvent)).scalars().one()
    assert evento.result == "conflict"


# ============================================================================ grant / correção 1
def test_grant_e_chamado_mesmo_com_payment_ja_approved_e_so_um_entitlement_permanece(auth_client, use_settings, factory, session):
    """Prova explícita pedida: Payment.status == "approved" + webhook approved -> grant_entitlement
    é chamado -> só um Entitlement permanece."""
    use_settings(mp_webhook_secret=SECRET)
    payment = factory.payment(status="approved", mp_payment_id="42", external_reference="ref-42", approved_at=utcnow())
    assert session.execute(select(Entitlement).where(Entitlement.payment_id == payment.id)).scalars().all() == []

    with patch("payments.client.get_payment", return_value=_resposta_get("42", "approved", "ref-42")):
        resposta = _post(auth_client, "42")

    assert resposta.status_code == 200
    entitlements = session.execute(select(Entitlement).where(Entitlement.payment_id == payment.id)).scalars().all()
    assert len(entitlements) == 1
    evento = session.execute(select(PaymentEvent)).scalars().one()
    assert evento.result == "granted"


def test_webhook_approved_repetido_nao_duplica_entitlement(auth_client, use_settings, factory, session):
    use_settings(mp_webhook_secret=SECRET)
    payment = factory.payment(status="pending", external_reference="ref-99")

    with patch("payments.client.get_payment", return_value=_resposta_get("99", "approved", "ref-99")):
        _post(auth_client, "99", request_id="req-a")
        _post(auth_client, "99", request_id="req-b")

    entitlements = session.execute(select(Entitlement).where(Entitlement.payment_id == payment.id)).scalars().all()
    assert len(entitlements) == 1
    eventos = session.execute(select(PaymentEvent)).scalars().all()
    assert len(eventos) == 2  # PaymentEvent permite duplicatas de propósito
    assert all(e.result == "granted" for e in eventos)


# ============================================================================ revoke
def test_refunded_revoga_entitlement_existente(auth_client, use_settings, factory, session):
    use_settings(mp_webhook_secret=SECRET)
    payment = factory.payment(status="approved", mp_payment_id="7", external_reference="ref-7")
    factory.entitlement(payment)

    with patch("payments.client.get_payment", return_value=_resposta_get("7", "refunded", "ref-7")):
        resposta = _post(auth_client, "7")

    assert resposta.status_code == 200
    entitlement = session.execute(select(Entitlement).where(Entitlement.payment_id == payment.id)).scalars().one()
    assert entitlement.status == "revoked"
    evento = session.execute(select(PaymentEvent)).scalars().one()
    assert evento.result == "revoked"


# ============================================================================ orphan / conflict / erro
def test_payment_inexistente_e_orphan_200(auth_client, use_settings, session):
    use_settings(mp_webhook_secret=SECRET)
    with patch("payments.client.get_payment", return_value=_resposta_get("123123", "approved", "ref-nunca-existiu")):
        resposta = _post(auth_client, "123123")

    assert resposta.status_code == 200
    evento = session.execute(select(PaymentEvent)).scalars().one()
    assert evento.result == "orphan"


def test_external_reference_incompativel_e_conflict_200(auth_client, use_settings, factory, session):
    use_settings(mp_webhook_secret=SECRET)
    payment = factory.payment(status="pending", mp_payment_id="55", external_reference="ref-55")

    with patch("payments.client.get_payment", return_value=_resposta_get("55", "approved", "ref-diferente")):
        resposta = _post(auth_client, "55")

    assert resposta.status_code == 200
    session.refresh(payment)
    assert payment.status == "pending"
    evento = session.execute(select(PaymentEvent)).scalars().one()
    assert evento.result == "conflict"


def test_falha_de_comunicacao_com_mercado_pago_e_502(auth_client, use_settings, session):
    use_settings(mp_webhook_secret=SECRET)
    from payments.errors import PaymentGatewayError

    with patch("payments.client.get_payment", side_effect=PaymentGatewayError("timeout")):
        resposta = _post(auth_client, "321")

    assert resposta.status_code == 502
    evento = session.execute(select(PaymentEvent)).scalars().one()
    assert evento.result == "error"
    assert evento.error is not None


# ============================================================================ segurança
def test_access_token_e_secret_nunca_aparecem_na_resposta(auth_client, use_settings, factory):
    segredo_token = "access-token-que-jamais-pode-vazar"
    use_settings(mp_webhook_secret=SECRET, mp_access_token=segredo_token)
    payment = factory.payment(status="pending", external_reference="ref-seg")

    with patch("payments.client.get_payment", return_value=_resposta_get("1010", "approved", "ref-seg")):
        resposta = _post(auth_client, "1010")

    assert segredo_token not in resposta.text
    assert SECRET not in resposta.text
