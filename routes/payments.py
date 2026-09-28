"""Rotas de pagamento (Etapa 10.2): criação do Payment interno + submit do Payment Brick.

Mesmo desenho de routes/generations.py: rotas FINAS. Toda regra de negócio mora em payments/ --
esta camada só resolve o usuário autenticado, valida a FORMA do corpo (Pydantic -- nunca aceita
preço/plano/external_reference/user_id onde não deveria) e traduz exceção para HTTP.

GET /api/payments/public-key expõe a Public Key do Mercado Pago ao frontend -- é a ÚNICA
credencial de pagamento que pode aparecer no browser; o Access Token nunca é lido por este
arquivo (só payments/client.py o lê, via config.get_settings())."""
import logging
import re
from typing import Literal

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from config import Settings, get_settings
from database.models import User
from database.session import get_session
from payments.errors import (
    InvalidPaymentMethodError,
    PaymentGatewayError,
    PaymentMethodMismatchError,
    PaymentNotFoundError,
    PlanNotPurchasableError,
    UnknownPlanError,
)
from payments.gateway import charge
from payments.service import create_payment, get_owned_pending_payment
from routes.deps import get_current_user

logger = logging.getLogger("minhoca")

router = APIRouter(prefix="/api", tags=["payments"])

NO_STORE = {"Cache-Control": "no-store"}


class CreatePaymentBody(BaseModel):
    """Só plan_code + method -- nenhum campo de preço/external_reference/user_id existe aqui,
    então nenhum deles pode chegar a create_payment vindo do corpo da requisição."""

    plan_code: str = Field(min_length=1, max_length=32)
    method: Literal["pix", "credit_card"]


class PayerBody(BaseModel):
    """Dados do pagador que o Payment Brick coleta -- e-mail obrigatório (MP exige para
    antifraude); identification é opcional aqui e repassado como veio (CPF etc., quando o Brick
    já pedir isso do usuário)."""

    email: str
    identification: dict | None = None


class SubmitPaymentBody(BaseModel):
    """O corpo que o onSubmit do Payment Brick entrega à nossa rota. NUNCA declara plan_code,
    amount, amount_cents, price, price_cents, external_reference nem user_id -- a rota
    simplesmente não tem como repassar o que não está aqui.

    `method` é o que o Brick efetivamente submeteu -- comparado contra Payment.method DENTRO de
    payments.gateway.charge, antes de qualquer chamada ao Mercado Pago (ver routes.payments.submit_payment)."""

    method: Literal["pix", "credit_card"]
    payment_method_id: str
    payer: PayerBody
    token: str | None = None
    installments: int | None = None
    issuer_id: str | None = None


class PaymentOut(BaseModel):
    """Resposta da criação -- nunca inclui external_reference (o frontend não precisa dele e não
    deveria conseguir vê-lo, ver payments/service.py::create_payment)."""

    payment_id: int
    amount_cents: int
    plan_code: str
    method: str
    status: str


class SubmitPaymentOut(BaseModel):
    payment_id: int
    status: str
    status_detail: str | None
    qr_code: str | None = None
    qr_code_base64: str | None = None
    ticket_url: str | None = None


_CAMEL_CASE_RE = re.compile(r"(?<!^)(?=[A-Z])")


def _code_for(exc: BaseException) -> str:
    name = exc.__class__.__name__
    if name.endswith("Error"):
        name = name[: -len("Error")]
    return _CAMEL_CASE_RE.sub("_", name).lower()


def _error_response(status_code: int, exc: BaseException, *, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"detail": detail, "code": _code_for(exc)}, headers=NO_STORE)


async def unknown_plan_handler(request, exc: UnknownPlanError) -> JSONResponse:
    return _error_response(422, exc, detail="Plano inválido.")


async def plan_not_purchasable_handler(request, exc: PlanNotPurchasableError) -> JSONResponse:
    return _error_response(422, exc, detail="Este plano não pode ser comprado.")


async def invalid_payment_method_handler(request, exc: InvalidPaymentMethodError) -> JSONResponse:
    return _error_response(422, exc, detail="Método de pagamento inválido.")


async def payment_not_found_handler(request, exc: PaymentNotFoundError) -> JSONResponse:
    """404 genérico e único -- Payment inexistente, de outro usuário, ou não mais pendente
    recebem exatamente a mesma resposta (mesma filosofia de generation_download_not_found_handler
    e batch_not_found_handler em routes/generations.py)."""
    return _error_response(404, exc, detail="Pagamento não encontrado.")


async def payment_method_mismatch_handler(request, exc: PaymentMethodMismatchError) -> JSONResponse:
    """Erro controlado (Etapa 10.2, item 4): a cobrança nunca chega a ser tentada quando o
    método submetido pelo Brick não bate com o Payment.method gravado na criação."""
    logger.warning("[PAYMENT] %s", exc)
    return _error_response(409, exc, detail="O método de pagamento não corresponde a este pagamento.")


async def payment_gateway_handler(request, exc: PaymentGatewayError) -> JSONResponse:
    logger.error("[PAYMENT] falha de gateway: %s", exc)
    return _error_response(502, exc, detail="Não foi possível processar o pagamento agora. Tente novamente.")


@router.get("/payments/public-key")
def get_public_key(cfg: Settings = Depends(get_settings)) -> dict:
    """Única rota que expõe uma credencial do Mercado Pago ao frontend -- a Public Key, que é
    justamente a credencial pensada para isso (nunca o Access Token, que só payments/client.py lê,
    e este arquivo nunca importa). `Depends(get_settings)` -- mesmo padrão de routes/deps.py e
    routes/auth.py -- lê a configuração no momento da chamada, não uma cópia fixada na
    importação do módulo (necessário para os testes poderem trocar a configuração)."""
    return {"public_key": cfg.mp_public_key}


@router.post("/payments", response_model=PaymentOut)
def create_payment_route(
    body: CreatePaymentBody,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> PaymentOut:
    payment = create_payment(db, user_id=user.id, plan_code=body.plan_code, method=body.method)
    return PaymentOut(
        payment_id=payment.id,
        amount_cents=payment.amount_cents,
        plan_code=payment.plan_code,
        method=payment.method,
        status=payment.status,
    )


@router.post("/payments/{payment_id}/submit", response_model=SubmitPaymentOut)
def submit_payment(
    payment_id: int,
    body: SubmitPaymentBody,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> SubmitPaymentOut:
    payment = get_owned_pending_payment(db, payment_id, user.id)
    brick_data = {
        "payment_method_id": body.payment_method_id,
        "payer": body.payer.model_dump(exclude_none=True),
        "token": body.token,
        "installments": body.installments,
        "issuer_id": body.issuer_id,
    }
    resultado = charge(db, payment, method=body.method, brick_data=brick_data)
    return SubmitPaymentOut(
        payment_id=resultado.payment.id,
        status=resultado.payment.status,
        status_detail=resultado.payment.status_detail,
        qr_code=resultado.qr_code,
        qr_code_base64=resultado.qr_code_base64,
        ticket_url=resultado.ticket_url,
    )
