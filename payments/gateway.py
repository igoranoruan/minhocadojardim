"""Duas responsabilidades separadas dentro da mesma fronteira de gateway (Mercado Pago):

1. `charge()` (Etapa 10.2): orquestra a cobrança de um Payment interno já criado (Etapa 10.1)
   contra o Mercado Pago -- recebe o Payment (já resolvido por ownership+status pela rota, via
   payments.service.get_owned_pending_payment) e os dados que o Payment Brick submeteu, monta o
   payload de POST /v1/payments, chama payments.client, e traduz a resposta de volta para o nosso
   domínio (Payment.status/status_detail/mp_payment_id/approved_at). NÃO chama
   grant_entitlement/revoke_entitlement -- mesmo quando o Mercado Pago responde "approved" na
   hora (ver tests/test_payments_gateway_architecture.py, que trava isso estruturalmente na
   própria função `charge`).

2. `reconcile_webhook_payment()` (Etapa 10.3): a confirmação AUTORITATIVA e definitiva, disparada
   pelo webhook (routes/webhooks.py) depois que a assinatura já foi validada. Consulta
   GET /v1/payments/{id} (payments.client.get_payment), localiza o Payment interno
   (payments.service.find_payment_for_webhook), trava por usuário, revalida external_reference, e
   é o ÚNICO lugar do sistema que chama grant_entitlement/revoke_entitlement -- por isso, e só
   aqui, este módulo importa services.entitlements.

   Etapa 8 (programa de indicação, aprovação do CÉREBRO): depois de um grant_entitlement bem
   sucedido, chama também services.referrals.process_entitlement_granted -- nunca antes, nunca em
   caso de erro. Essa função não decide preço/plano, não lê Payment, e nunca levanta (qualquer
   erro do programa de indicação é logado e engolido ali mesmo); é só a ponte entre "um pagamento
   foi aprovado" e "talvez isso dispare ou aplique uma recompensa de indicação".
"""
import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import Entitlement, Payment
from payments import client
from payments.errors import PaymentMethodMismatchError
from payments.service import find_payment_for_webhook
from services.entitlements import grant_entitlement, revoke_entitlement
from services.locks import lock_user_row
from services.referrals import process_entitlement_granted
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


# ============================================================================ Etapa 10.3 (webhook)

# Estados que a reconciliação do webhook sabe tratar -- conjunto COMPLETO de PAYMENT_STATUSES
# (database/models/payment.py), diferente de _STATUS_GERIDOS acima (10.2), que só precisa dos 3
# estados que uma cobrança SÍNCRONA pode devolver. Qualquer valor fora deste conjunto (ex.:
# "in_process", "authorized") é mapeado defensivamente para "pending" -- mesma filosofia de
# _status_local, nunca aprovando/revogando sem confirmação inequívoca.
_STATUS_GERIDOS_WEBHOOK = {"approved", "pending", "rejected", "cancelled", "refunded", "charged_back"}


@dataclass(frozen=True)
class ReconcileResult:
    """O que routes/webhooks.py precisa para decidir o HTTP e atualizar o PaymentEvent. `outcome`
    NUNCA é comunicado como exceção para "orphan"/"conflict"/"no_op" -- os três ainda respondem
    HTTP 200 ao Mercado Pago (não é um erro de comunicação, é uma decisão de negócio já tomada)."""

    outcome: str  # "granted" | "revoked" | "no_op" | "orphan" | "conflict"
    payment: Payment | None
    detail: str | None = None


def _status_local_webhook(status_mp: str | None) -> str:
    """Mesmo espírito defensivo de _status_local (10.2), mas com o conjunto COMPLETO de estados
    que o webhook (10.3) sabe reconciliar -- inclui refunded/charged_back/cancelled, que a
    cobrança síncrona da 10.2 nunca precisa produzir."""
    if status_mp in _STATUS_GERIDOS_WEBHOOK:
        return status_mp
    logger.warning(
        "[PAYMENT] status inesperado do Mercado Pago no webhook (%r) -- tratado como 'pending'", status_mp
    )
    return "pending"


def reconcile_webhook_payment(
    db: Session, *, mp_payment_id: str, now: datetime | None = None
) -> ReconcileResult:
    """Reconciliação autoritativa de um webhook JÁ AUTENTICADO (Etapa 10.3): consulta
    GET /v1/payments/{mp_payment_id} (payments.client.get_payment), localiza o Payment interno
    (payments.service.find_payment_for_webhook -- NUNCA por dado do próprio webhook além do
    mp_payment_id usado para consultar), trava por usuário, revalida external_reference, aplica a
    transição e decide grant/revoke.

    Regra de grant/revoke (Etapa 10.3, correção 1 -- OBRIGATÓRIA): a decisão depende SÓ do status
    autoritativo mais recente, NUNCA de uma "transição" a partir do status local anterior.
    grant_entitlement é chamado sempre que o status autoritativo é "approved" e o Payment está
    íntegro -- mesmo que Payment.status já fosse "approved" (a 10.2 pode deixar isso sem nunca ter
    concedido entitlement). revoke_entitlement é chamado sempre que existir um Entitlement para o
    payment_id e o status autoritativo for "refunded"/"charged_back" -- mesmo que Payment.status já
    fosse esse mesmo valor. Ambas as chamadas são absorvidas pela idempotência já existente delas
    (por payment_id) -- nenhuma lógica de "já processei isso antes" é reimplementada aqui.

    NUNCA usa user_id/plan_code/external_reference vindos do corpo do webhook -- só os que já
    estavam gravados no nosso Payment, encontrado por payments.service.find_payment_for_webhook.

    Levanta PaymentGatewayError se a consulta ao Mercado Pago falhar (propaga de
    payments.client.get_payment -- routes/webhooks.py traduz para HTTP 502, permitindo o Mercado
    Pago reenviar a notificação mais tarde)."""
    resposta = client.get_payment(mp_payment_id)
    corpo = resposta.get("response") or {}

    mp_status = corpo.get("status")
    mp_status_detail = corpo.get("status_detail")
    mp_external_reference = corpo.get("external_reference")

    payment = find_payment_for_webhook(db, mp_payment_id=mp_payment_id, external_reference=mp_external_reference)
    if payment is None:
        return ReconcileResult(
            outcome="orphan", payment=None,
            detail=f"Nenhum Payment local para mp_payment_id={mp_payment_id!r}.",
        )

    lock_user_row(db, payment.user_id)  # só agora: user_id só é conhecido depois de localizar o Payment
    payment = db.execute(
        select(Payment).where(Payment.id == payment.id).execution_options(populate_existing=True)
    ).scalar_one()

    if payment.mp_payment_id and payment.mp_payment_id != mp_payment_id:
        db.commit()  # nada a gravar: só libera o lock
        return ReconcileResult(
            outcome="conflict", payment=payment,
            detail="mp_payment_id encontrado pertence a outro Payment (colisão de external_reference).",
        )
    if payment.external_reference != mp_external_reference:
        db.commit()
        return ReconcileResult(
            outcome="conflict", payment=payment,
            detail="external_reference da resposta autoritativa não corresponde ao Payment local.",
        )

    status_local = _status_local_webhook(mp_status)

    if payment.status in ("refunded", "charged_back") and status_local == "approved":
        # Reversão nunca aplicada (Etapa 10.3): um Payment já reembolsado/estornado não volta a
        # ser aprovado por uma notificação posterior -- isso é inconsistência, não reconciliação.
        db.commit()
        return ReconcileResult(
            outcome="conflict", payment=payment,
            detail="reversão refunded/charged_back -> approved recusada.",
        )

    if payment.mp_payment_id is None:
        payment.mp_payment_id = mp_payment_id
    payment.status = status_local
    payment.status_detail = mp_status_detail
    if status_local == "approved" and payment.approved_at is None:
        payment.approved_at = resolve_now(now)
    if status_local in ("refunded", "charged_back") and payment.refunded_at is None:
        payment.refunded_at = resolve_now(now)

    if status_local == "approved":
        # SEMPRE chamado -- nunca condicionado a Payment.status ter sido "pending" antes (correção 1).
        entitlement = grant_entitlement(
            db, user_id=payment.user_id, payment_id=payment.id, plan_code=payment.plan_code, now=now
        )
        # Etapa 8 (programa de indicação, aprovação do CÉREBRO): chamado DEPOIS do grant_entitlement
        # já ter commitado com sucesso -- nunca pode atrasar nem arriscar a concessão do acesso pago
        # em si. process_entitlement_granted nunca levanta (engole e loga qualquer erro próprio),
        # então uma falha no programa de indicação nunca vira um 502 para o Mercado Pago nem repete
        # a notificação do webhook à toa.
        process_entitlement_granted(db, user_id=payment.user_id, entitlement=entitlement, now=now)
        return ReconcileResult(outcome="granted", payment=payment)

    if status_local in ("refunded", "charged_back"):
        entitlement = db.execute(
            select(Entitlement).where(Entitlement.payment_id == payment.id)
        ).scalar_one_or_none()
        if entitlement is not None:
            # SEMPRE chamado quando existe Entitlement -- nunca condicionado a detectar a
            # transição approved->refunded (correção 1); revoke_entitlement já é idempotente.
            revoke_entitlement(db, entitlement_id=entitlement.id, reason=status_local)
            return ReconcileResult(outcome="revoked", payment=payment)
        db.commit()  # nada a revogar: nunca existiu Entitlement para este payment_id
        return ReconcileResult(outcome="no_op", payment=payment)

    db.commit()  # pending/rejected/cancelled: só o Payment é atualizado, nenhuma ação de entitlement
    return ReconcileResult(outcome="no_op", payment=payment)
