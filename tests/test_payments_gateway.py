"""payments/gateway.py::charge (Etapa 10.2): monta o payload para o Mercado Pago, converte o
preço com Decimal, usa a X-Idempotency-Key certa, e traduz a resposta de volta para o Payment --
sempre com payments.client mockado (nenhum teste desta suíte fala com a rede real).
"""
from unittest.mock import patch

import pytest

from payments.errors import PaymentMethodMismatchError
from payments.gateway import charge
from payments.service import create_payment


def _resposta_mp(status="approved", *, status_detail="accredited", ponto_de_interacao=None):
    corpo = {"id": 123456789, "status": status, "status_detail": status_detail}
    if ponto_de_interacao is not None:
        corpo["point_of_interaction"] = ponto_de_interacao
    return {"status": 201, "response": corpo}


def _brick_cartao(**overrides):
    dados = {
        "payment_method_id": "master",
        "payer": {"email": "user@example.com"},
        "token": "tok_abc123",
        "installments": 1,
        "issuer_id": "25",
    }
    dados.update(overrides)
    return dados


def _brick_pix(**overrides):
    dados = {"payment_method_id": "pix", "payer": {"email": "user@example.com"}}
    dados.update(overrides)
    return dados


def test_transaction_amount_convertido_com_decimal_a_partir_do_catalogo(factory, session):
    """WEEKLY_PLAN.price_cents == 990 -> transaction_amount == 9.90 (reais), nunca centavos."""
    usuario = factory.user()
    payment = create_payment(session, user_id=usuario.id, plan_code="weekly", method="credit_card")

    with patch("payments.client.create_payment", return_value=_resposta_mp()) as mock_cliente:
        charge(session, payment, method="credit_card", brick_data=_brick_cartao())

    payload_enviado = mock_cliente.call_args.args[0]
    assert payload_enviado["transaction_amount"] == 9.90


def test_external_reference_do_payment_e_enviado_ao_mercado_pago(factory, session):
    usuario = factory.user()
    payment = create_payment(session, user_id=usuario.id, plan_code="weekly", method="pix")

    with patch("payments.client.create_payment", return_value=_resposta_mp(status="pending")) as mock_cliente:
        charge(session, payment, method="pix", brick_data=_brick_pix())

    payload_enviado = mock_cliente.call_args.args[0]
    assert payload_enviado["external_reference"] == payment.external_reference


def test_idempotency_key_e_exatamente_o_external_reference_do_payment(factory, session):
    usuario = factory.user()
    payment = create_payment(session, user_id=usuario.id, plan_code="monthly", method="pix")

    with patch("payments.client.create_payment", return_value=_resposta_mp(status="pending")) as mock_cliente:
        charge(session, payment, method="pix", brick_data=_brick_pix())

    assert mock_cliente.call_args.kwargs["idempotency_key"] == payment.external_reference


def test_approved_grava_status_e_approved_at(factory, session):
    usuario = factory.user()
    payment = create_payment(session, user_id=usuario.id, plan_code="weekly", method="credit_card")

    with patch("payments.client.create_payment", return_value=_resposta_mp(status="approved")):
        resultado = charge(session, payment, method="credit_card", brick_data=_brick_cartao())

    assert resultado.payment.status == "approved"
    assert resultado.payment.approved_at is not None
    assert resultado.payment.mp_payment_id == "123456789"
    assert resultado.payment.status_detail == "accredited"


def test_pending_nao_grava_approved_at(factory, session):
    usuario = factory.user()
    payment = create_payment(session, user_id=usuario.id, plan_code="weekly", method="pix")

    with patch("payments.client.create_payment", return_value=_resposta_mp(status="pending", status_detail="pending_waiting_transfer")):
        resultado = charge(session, payment, method="pix", brick_data=_brick_pix())

    assert resultado.payment.status == "pending"
    assert resultado.payment.approved_at is None


def test_rejected_nao_grava_approved_at(factory, session):
    usuario = factory.user()
    payment = create_payment(session, user_id=usuario.id, plan_code="weekly", method="credit_card")

    with patch(
        "payments.client.create_payment",
        return_value=_resposta_mp(status="rejected", status_detail="cc_rejected_other_reason"),
    ):
        resultado = charge(session, payment, method="credit_card", brick_data=_brick_cartao())

    assert resultado.payment.status == "rejected"
    assert resultado.payment.approved_at is None
    assert resultado.payment.status_detail == "cc_rejected_other_reason"


def test_status_desconhecido_do_mercado_pago_vira_pending_em_vez_de_quebrar_o_check_do_banco(factory, session):
    """Nenhum status fora de approved/pending/rejected (Etapa 10.2, item 10) pode chegar a violar
    o CHECK ck_payments_status_valido -- "in_process" é um status real do MP não coberto nesta
    etapa; deve virar "pending", nunca uma exceção nem um valor fora do enum."""
    usuario = factory.user()
    payment = create_payment(session, user_id=usuario.id, plan_code="weekly", method="credit_card")

    with patch("payments.client.create_payment", return_value=_resposta_mp(status="in_process")):
        resultado = charge(session, payment, method="credit_card", brick_data=_brick_cartao())

    assert resultado.payment.status == "pending"


def test_token_do_cartao_nunca_e_persistido_em_nenhuma_coluna_do_payment(factory, session):
    usuario = factory.user()
    payment = create_payment(session, user_id=usuario.id, plan_code="weekly", method="credit_card")
    token_secreto = "tok_este_valor_nunca_pode_ficar_no_banco"

    with patch("payments.client.create_payment", return_value=_resposta_mp(status="approved")):
        resultado = charge(session, payment, method="credit_card", brick_data=_brick_cartao(token=token_secreto))

    valores_das_colunas = [getattr(resultado.payment, c.name) for c in resultado.payment.__table__.columns]
    assert token_secreto not in valores_das_colunas


def test_metodo_incompativel_nao_chega_a_chamar_o_gateway_e_nao_cobra(factory, session):
    """Payment criado como 'pix' submetido como cartão: erro controlado, ZERO chamada ao
    Mercado Pago (Etapa 10.2, item 4)."""
    usuario = factory.user()
    payment = create_payment(session, user_id=usuario.id, plan_code="weekly", method="pix")

    with patch("payments.client.create_payment") as mock_cliente:
        with pytest.raises(PaymentMethodMismatchError):
            charge(session, payment, method="credit_card", brick_data=_brick_cartao())

    mock_cliente.assert_not_called()
    assert payment.status == "pending"  # nada mudou -- nem tentativa de cobrança


def test_pix_devolve_qr_code_da_resposta_do_mercado_pago(factory, session):
    usuario = factory.user()
    payment = create_payment(session, user_id=usuario.id, plan_code="weekly", method="pix")
    resposta = _resposta_mp(
        status="pending",
        ponto_de_interacao={
            "transaction_data": {
                "qr_code": "00020126-copia-e-cola",
                "qr_code_base64": "aGVsbG8=",
                "ticket_url": "https://mercadopago.example/ticket/123",
            }
        },
    )

    with patch("payments.client.create_payment", return_value=resposta):
        resultado = charge(session, payment, method="pix", brick_data=_brick_pix())

    assert resultado.qr_code == "00020126-copia-e-cola"
    assert resultado.qr_code_base64 == "aGVsbG8="
    assert resultado.ticket_url == "https://mercadopago.example/ticket/123"


def test_pix_nao_persiste_qr_code_em_nenhuma_coluna_do_payment(factory, session):
    usuario = factory.user()
    payment = create_payment(session, user_id=usuario.id, plan_code="weekly", method="pix")
    resposta = _resposta_mp(
        status="pending",
        ponto_de_interacao={"transaction_data": {"qr_code": "algo-efemero", "qr_code_base64": "outro-efemero"}},
    )

    with patch("payments.client.create_payment", return_value=resposta):
        charge(session, payment, method="pix", brick_data=_brick_pix())

    colunas = {c.name for c in payment.__table__.columns}
    assert "qr_code" not in colunas
    assert "qr_code_base64" not in colunas
    valores = [getattr(payment, c) for c in colunas]
    assert "algo-efemero" not in valores
    assert "outro-efemero" not in valores
