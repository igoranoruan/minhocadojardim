"""Orquestra a cobrança de um Payment interno já criado (Etapa 10.1) contra o Mercado Pago
(Etapa 10.2): recebe o Payment (já resolvido por ownership+status pela rota, via
payments.service.get_owned_pending_payment) e os dados que o Payment Brick submeteu, monta o
payload de POST /v1/payments, chama payments.client (o único arquivo que fala com o SDK), e
traduz a resposta de volta para o nosso domínio (Payment.status/status_detail/mp_payment_id/
approved_at).

NÃO importa services.entitlements. NÃO chama grant_entitlement/revoke_entitlement -- mesmo
quando o Mercado Pago responde "approved" na hora. A concessão definitiva do acesso é
responsabilidade exclusiva da Etapa 10.3 (webhook + confirmação autoritativa) -- ver
payments/__init__.py.
"""
import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy.orm import Session

from database.models import Payment
from payments import client
from payments.errors import PaymentMethodMismatchError
from utils.time_sp import resolve_now

logger = logging.getLogger("minhoca")

# Só estes 3 status são geridos nesta etapa (approved/pending/rejected -- ver Etapa 10.2, item 10).
# Qualquer outro valor que o Mercado Pago venha a devolver (ex.: "in_process", "authorized") é
# tratado como "pending" (nunca rejeitado silenciosamente, nunca aprovado sem confirmação) --
# decisão defensiva: Payment.status tem um CHECK de banco que só aceita um conjunto fixo de
# valores (database/models/payment.py::PAYMENT_STATUSES), então gravar um status fora dele
# quebraria o commit; "pending" é sempre um valor seguro para "ainda não sabemos o resultado
# final", que é exatamente o que a Etapa 10.3 (webhook) vai reconciliar de verdade.
_STATUS_GERIDOS = {"approved", "pending", "rejected"}


@dataclass(frozen=True)
class ChargeResult:
    """O que routes/payments.py precisa para responder ao frontend -- nunca o dicionário cru do
    Mercado Pago, que a rota não deveria conhecer."""

    payment: Payment
    qr_code: str | None
    qr_code_base64: str | None
    ticket_url: str | None


def _transaction_amount(amount_cents: int) -> float:
    """amount_cents (inteiro, centavos, SEMPRE lido de Payment -- nunca do chamador) convertido
    para reais com Decimal (nunca float puro, que arredondaria de forma imprevisível)."""
    reais = (Decimal(amount_cents) / Decimal(100)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return float(reais)


def _build_payload(payment: Payment, brick_data: dict) -> dict:
    """Monta o corpo de POST /v1/payments. external_reference vem SEMPRE de payment.external_reference
    (Etapa 10.1) -- nunca gerado aqui, nunca aceito de brick_data (routes/payments.py nem declara
    esse campo no schema do corpo, então brick_data nunca o contém)."""
    payload: dict = {
        "transaction_amount": _transaction_amount(payment.amount_cents),
        "external_reference": payment.external_reference,
        "description": f"Minhoca de Jardim - plano {payment.plan_code}",
        "payment_method_id": brick_data["payment_method_id"],
        "payer": brick_data["payer"],
    }
    if payment.method == "credit_card":
        # Token de uso único do Brick -- nunca persistido em lugar nenhum (nem aqui, nem no
        # Payment): só passa por este payload, na memória da requisição.
        payload["token"] = brick_data["token"]
        payload["installments"] = brick_data["installments"]
        if brick_data.get("issuer_id"):
            payload["issuer_id"] = brick_data["issuer_id"]
    return payload


def _status_local(status_mp: str | None) -> str:
    if status_mp in _STATUS_GERIDOS:
        return status_mp
    logger.warning("[PAYMENT] status inesperado do Mercado Pago (%r) -- tratado como 'pending'", status_mp)
    return "pending"


def charge(
    db: Session, payment: Payment, *, method: str, brick_data: dict, now: datetime | None = None,
) -> ChargeResult:
    """Cobra um Payment "pending" já resolvido por ownership (ver
    payments.service.get_owned_pending_payment, chamada pela rota ANTES desta função).

    `method` é o método que o Payment Brick efetivamente submeteu ("pix"/"credit_card") --
    validado AQUI contra payment.method, ANTES de qualquer chamada ao Mercado Pago: uma
    inconsistência (Payment criado como "pix" submetido como cartão, ou vice-versa) nunca chega
    a ser cobrada -- levanta PaymentMethodMismatchError, erro controlado, sem tocar o gateway.

    Grava em Payment: mp_payment_id, status (approved/pending/rejected -- ver _status_local),
    status_detail, e approved_at (só quando status == "approved"; None em pending/rejected).

    NÃO chama grant_entitlement/revoke_entitlement -- ver o docstring do módulo.
    """
    if method != payment.method:
        raise PaymentMethodMismatchError(
            f"Payment {payment.id} foi criado como {payment.method!r}, mas o Brick submeteu {method!r}."
        )

    payload = _build_payload(payment, brick_data)
    resposta = client.create_payment(payload, idempotency_key=payment.external_reference)
    corpo = resposta.get("response") or {}

    mp_payment_id = corpo.get("id")
    payment.mp_payment_id = str(mp_payment_id) if mp_payment_id is not None else None
    payment.status = _status_local(corpo.get("status"))
    payment.status_detail = corpo.get("status_detail")
    payment.approved_at = resolve_now(now) if payment.status == "approved" else None
    db.commit()

    dados_pix = (corpo.get("point_of_interaction") or {}).get("transaction_data") or {}
    return ChargeResult(
        payment=payment,
        qr_code=dados_pix.get("qr_code"),
        qr_code_base64=dados_pix.get("qr_code_base64"),
        ticket_url=dados_pix.get("ticket_url"),
    )
