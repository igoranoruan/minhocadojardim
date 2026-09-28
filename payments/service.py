"""Fundação interna do sistema de pagamentos (Etapa 10.1) + resolução de ownership para o
checkout (Etapa 10.2). create_payment cria o Payment "pending" que antecede qualquer integração
com o Mercado Pago -- não fala com nenhum gateway externo, não processa webhook, não concede
entitlement. get_owned_pending_payment (10.2) é a ÚNICA forma pela qual a rota de submit
(routes/payments.py) resolve QUAL Payment a rota está manipulando -- nunca um SELECT solto por
id na rota.

A integração com o Mercado Pago (payments/gateway.py, payments/client.py, Etapa 10.2) é uma
camada que vem DEPOIS deste arquivo, nunca dentro dele -- o mesmo desenho que download/service.py
já usa para o yt-dlp: quem chama create_payment/get_owned_pending_payment não precisa saber (e
este arquivo não sabe) que um gateway existe.
"""
import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import Payment
from database.models.payment import PAYMENT_METHODS
from payments.errors import InvalidPaymentMethodError, PaymentNotFoundError, PlanNotPurchasableError
from payments.reference import generate_external_reference
from services.locks import lock_user_row
from services.plans import get_plan

logger = logging.getLogger("minhoca")


def create_payment(
    db: Session, *, user_id: int, plan_code: str, method: str, now: datetime | None = None,
) -> Payment:
    """Cria (ou devolve, se já existir um "pending" idêntico) um Payment para este usuário.

    amount_cents vem SEMPRE de plan.price_cents -- esta função não tem (e não deve ganhar) um
    parâmetro de preço; um cliente nunca tem como influenciar o valor cobrado (ver
    tests/test_payments_architecture.py, que trava isso estruturalmente).

    Idempotente por (user_id, plan_code, method, status="pending"): repetir a mesma chamada
    enquanto o Payment anterior ainda estiver "pending" devolve o MESMO registro, nunca cria um
    segundo -- protege contra duplo-clique/retry de rede sem precisar de uma coluna nova. Uma
    segunda compra do MESMO plano é permitida (entitlements se empilham) e cria um Payment novo,
    mas só depois que o anterior sair de "pending" (aprovado, rejeitado, cancelado etc.).

    NÃO fala com o Mercado Pago. NÃO chama services.entitlements.grant_entitlement -- ambas as
    responsabilidades pertencem a etapas posteriores (10.2+).

    Levanta:
    - services.plans.UnknownPlanError -- plan_code não existe no catálogo (reexportada, não
      interceptada aqui: ver payments/errors.py).
    - PlanNotPurchasableError -- plan_code existe mas não é compra (ex.: "free").
    - InvalidPaymentMethodError -- method fora de PAYMENT_METHODS.
    - services.locks.UserNotFoundError -- user_id não existe (levantada por lock_user_row).

    `now` não é usado por nenhuma regra desta etapa (created_at/updated_at do Payment já vêm do
    default utcnow() do próprio model) -- o parâmetro existe só para manter a MESMA assinatura de
    services.usage.reserve_batch/services.entitlements.grant_entitlement, e para já deixar o
    espaço reservado caso uma etapa futura precise de um "agora" injetável aqui (ex.: expiração
    de uma cobrança PIX). Aceitar e ignorar deliberadamente, em vez de omitir, evita que uma etapa
    futura precise mudar a assinatura só para adicionar isso.
    """
    plan = get_plan(plan_code)  # levanta UnknownPlanError (services.plans) se não existir -- nunca interceptada aqui
    if not plan.paid:
        raise PlanNotPurchasableError(f"O plano {plan_code!r} não pode ser comprado.")
    if method not in PAYMENT_METHODS:
        raise InvalidPaymentMethodError(f"Método de pagamento inválido: {method!r}")

    lock_user_row(db, user_id)  # serializa por usuário -- mesma primeira instrução de grant_entitlement/reserve_batch

    existente = db.execute(
        select(Payment)
        .where(
            Payment.user_id == user_id,
            Payment.plan_code == plan.code,
            Payment.method == method,
            Payment.status == "pending",
        )
        .execution_options(populate_existing=True)  # sempre o estado atual do banco após o lock (mesmo padrão de entitlements.py)
    ).scalars().first()
    if existente is not None:
        db.commit()  # nada a gravar: só libera o lock, mesmo padrão de grant_entitlement ao repetir payment_id
        return existente

    payment = Payment(
        user_id=user_id,
        plan_code=plan.code,
        amount_cents=plan.price_cents,
        method=method,
        status="pending",
        external_reference=generate_external_reference(),
        mp_payment_id=None,
        status_detail=None,
        approved_at=None,
        refunded_at=None,
    )
    db.add(payment)
    db.flush()
    db.commit()
    logger.info(
        "[PAYMENT] criado id=%s user_id=%s plano=%s method=%s amount_cents=%s",
        payment.id, user_id, plan.code, method, payment.amount_cents,
    )
    return payment


def get_owned_pending_payment(db: Session, payment_id: int, user_id: int) -> Payment:
    """Etapa 10.2: o Payment que a rota de submit (routes/payments.py) vai cobrar -- resolvido
    SEMPRE pelos três critérios ao mesmo tempo (id + user_id + status="pending"), nunca por um
    SELECT solto pelo id seguido de uma comparação de dono separada (mesma filosofia de
    services.usage.get_owned_batch/get_downloadable_generation).

    Levanta PaymentNotFoundError (404 genérico e único) quando o Payment não existe, quando é de
    outro usuário, OU quando já não está mais "pending" (foi cobrado, recusado, ou cancelado) --
    as três situações são deliberadamente indistinguíveis para quem chama a rota, exatamente como
    BatchNotFoundError já faz para lotes."""
    payment = db.execute(
        select(Payment).where(
            Payment.id == payment_id,
            Payment.user_id == user_id,
            Payment.status == "pending",
        )
    ).scalar_one_or_none()
    if payment is None:
        raise PaymentNotFoundError(f"Payment {payment_id} não encontrado, não é seu, ou não está mais pendente.")
    return payment
