"""Rota do "Teste VIP de 24h" por link de campanha (ETAPA B7, pedido e aprovação do Igor,
29/10/2026).

POST /api/trial/redeem   ativa o teste para a conta autenticada

Rota FINA, mesmo desenho de routes/payments.py/routes/me.py: toda regra mora em services/trial.py
-- esta camada só resolve o usuário autenticado (login é OBRIGATÓRIO, get_current_user, nunca a
identidade Free anônima), lê o IP (mesmo client_ip de routes/deps.py) e traduz exceção para HTTP.
"""
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from config import TRIAL_CAMPAIGN_CODE_MAX_LENGTH, TRIAL_DEVICE_ID_MAX_LENGTH
from database.models import User
from database.session import get_session
from routes.deps import client_ip, get_current_user
from services.trial import (
    InvalidCampaignCodeError,
    TrialError,
    TrialNotEligibleError,
    TrialRedeemRateLimitedError,
    redeem_trial,
)

logger = logging.getLogger("minhoca")

router = APIRouter(prefix="/api/trial", tags=["trial"])

NO_STORE = {"Cache-Control": "no-store"}


def _code_for(exc: BaseException) -> str:
    name = exc.__class__.__name__
    if name.endswith("Error"):
        name = name[: -len("Error")]
    out = []
    for i, ch in enumerate(name):
        if ch.isupper() and i > 0:
            out.append("_")
        out.append(ch.lower())
    return "".join(out)


async def trial_error_handler(request, exc: TrialError) -> JSONResponse:
    headers = dict(NO_STORE)
    status_code = exc.status_code
    if isinstance(exc, TrialRedeemRateLimitedError):
        headers["Retry-After"] = str(exc.retry_after_seconds)
    return JSONResponse(
        status_code=status_code,
        content={"detail": exc.detail, "code": _code_for(exc)},
        headers=headers,
    )


class RedeemTrialBody(BaseModel):
    """`campaign` é o "?c=" capturado pelo frontend (static/index.html) -- esta rota NUNCA lê a
    URL/query string diretamente, só o que o frontend já validou e reenviou no corpo.
    `device_id` é o identificador aleatório gerado pelo FRONTEND (localStorage/cookie) -- esta
    rota nunca gera nem lê um cookie de aparelho próprio; services/trial.py grava só o hash."""

    campaign: str = Field(min_length=1, max_length=TRIAL_CAMPAIGN_CODE_MAX_LENGTH)
    device_id: str = Field(min_length=1, max_length=TRIAL_DEVICE_ID_MAX_LENGTH)


class RedeemTrialOut(BaseModel):
    expires_at: datetime


@router.post("/redeem", response_model=RedeemTrialOut)
def redeem_trial_route(
    body: RedeemTrialBody,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> RedeemTrialOut:
    resultado = redeem_trial(
        db, user=user, campaign=body.campaign, device_id=body.device_id, ip=client_ip(request),
    )
    return RedeemTrialOut(expires_at=resultado.expires_at)
