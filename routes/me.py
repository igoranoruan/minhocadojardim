"""Status do usuário autenticado (UX-1): GET /api/me/status.

Rota fina e SOMENTE LEITURA: nenhuma escrita no banco, nenhuma regra de negócio nova. Todo dado
vem de services/user_status.py::get_user_status, que por sua vez só compõe (sem decidir nada
novo) as duas fontes já existentes e já testadas --

- services/usage.py::get_allowance -- plano efetivo (Allowance.plan é a fonte AUTORITATIVA do
  plano devolvido aqui) e uso (used/limit/remaining/period). Esta rota NUNCA consulta a tabela
  generations diretamente nem recalcula esses números.
- services/entitlements.py::get_current_entitlement -- expires_at quando existe um entitlement
  vigente. Free, entitlement expirado ou revoked já viram None por conta própria.

routes/me.py NÃO importa services.usage/services.entitlements diretamente -- só
services.user_status (ver o docstring desse módulo para o porquê: proteção arquitetural de
tests/test_usage_architecture.py::test_nenhuma_regra_de_cota_em_rotas_main_ou_frontend, que
proíbe qualquer rota fora de uma lista nomeada de importar os módulos de regra de cota
diretamente).

Autenticação: routes/deps.py::get_generation_user (Free anônimo -- aprovação do CÉREBRO): sessão
autenticada OU identidade anônima por cookie, nunca 401 -- o visitante precisa ver "usados X/5"
mesmo sem login (é isso que permite ao frontend encaminhá-lo para Planos na 6ª tentativa, sem
jamais abrir o modal de login). routes/payments.py continua em get_current_user (login continua
obrigatório para pagar), sem nenhuma mudança.

Fora do contrato de propósito (UX-1): preço, batch, e-mail, ids internos, dias restantes -- ver o
relatório da microauditoria/autorização desta etapa.
"""
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database.models import User
from database.session import get_session
from routes.deps import get_current_user, get_generation_user
from services.generation_history import get_generation_history
from config import REFERRAL_MILESTONE_PLAN_CODE, REFERRAL_MILESTONE_REWARD_DAYS
from services.referrals import (
    AlreadyClaimedError,
    InvalidReferralCodeError,
    SelfReferralError,
    claim_referral,
    get_last_applied_reward,
    get_or_create_referral_code,
)
from services.user_status import get_user_status

router = APIRouter(prefix="/api/me", tags=["me"])

# Etapa 7 (revisão de UX, aprovação do CÉREBRO): teto de itens que GET /api/me/generations aceita
# no parâmetro `limit` -- mesmo MAX_HISTORY_LIMIT de services/usage.py (list_recent_generations já
# trava no mesmo valor por conta própria; esta constante só existe para o Query(le=...) documentar
# o teto na própria assinatura da rota, sem importar services.usage aqui -- ver o motivo completo
# no docstring de services/generation_history.py).
MAX_HISTORY_LIMIT = 50


class PlanOut(BaseModel):
    code: str
    name: str


class UsageOut(BaseModel):
    used: int
    limit: int
    remaining: int
    period: str


class EntitlementOut(BaseModel):
    """Só expires_at -- nenhum outro campo do Entitlement (id, status, etc.) pertence a este
    contrato mínimo (ver autorização do UX-1). Pydantic serializa datetime timezone-aware em ISO
    8601 com offset (ex.: "2026-10-22T23:59:59+00:00") sem nenhuma conversão nossa -- o valor que
    sai daqui é exatamente o Entitlement.expires_at que já vem do banco (UTCDateTime, sempre
    timezone-aware; ver database/types.py)."""

    expires_at: datetime


class StatusOut(BaseModel):
    plan: PlanOut
    usage: UsageOut
    entitlement: EntitlementOut | None = None


@router.get("/status", response_model=StatusOut)
def get_status(
    user: User = Depends(get_generation_user),
    db: Session = Depends(get_session),
) -> StatusOut:
    status = get_user_status(db, user.id)

    entitlement_out = None
    if status.expires_at is not None:
        entitlement_out = EntitlementOut(expires_at=status.expires_at)

    return StatusOut(
        plan=PlanOut(code=status.plan.code, name=status.plan.name),
        usage=UsageOut(
            used=status.used,
            limit=status.limit,
            remaining=status.remaining,
            period=status.plan.period,
        ),
        entitlement=entitlement_out,
    )


# ---------------------------------------------------------------------- Etapa 7: histórico (UX)
class GenerationHistoryOut(BaseModel):
    """Contrato mínimo de exibição -- nunca os campos internos do modelo Generation (entitlement_id,
    request_id, batch_id, etc., sem relação com o que o histórico do usuário precisa mostrar).
    `id` aqui é só o identificador necessário para montar o link de download
    (GET /api/generations/{id}/download, já existente -- esta rota não cria nenhum link novo)."""

    id: int
    created_at: datetime
    platform: str | None
    status: str
    downloadable: bool
    display_filename: str | None


@router.get("/generations", response_model=list[GenerationHistoryOut])
def get_generations_history(
    limit: int = Query(default=20, ge=1, le=MAX_HISTORY_LIMIT),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[GenerationHistoryOut]:
    """Etapa 7 da revisão de UX (aprovação do CÉREBRO): "meus últimos downloads". SOMENTE login
    de verdade (get_current_user, 401 sem sessão) -- diferente de /status, que também aceita a
    identidade Free anônima: um histórico PERSISTENTE faz sentido para uma conta, não para um
    cookie de dispositivo sem login, que pode ser apagado ou trocado a qualquer momento.

    Rota fina e SOMENTE LEITURA, mesmo padrão de /status: toda a composição (quais gerações, e se
    cada uma ainda está disponível para download) mora em services/generation_history.py, que por
    sua vez só delega para services.usage.list_recent_generations (nunca duplicando a checagem de
    disponibilidade que já existe em services.usage.get_downloadable_generation)."""
    historico = get_generation_history(db, user.id, limit=limit)
    return [
        GenerationHistoryOut(
            id=item.id,
            created_at=item.created_at,
            platform=item.platform,
            status=item.status,
            downloadable=item.downloadable,
            display_filename=item.display_filename,
        )
        for item in historico
    ]


# ------------------------------------------------------------------- Etapa 8: programa de indicação
async def invalid_referral_code_handler(request, exc: InvalidReferralCodeError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": "Código de indicação inválido.", "code": "invalid_referral_code"})


async def self_referral_handler(request, exc: SelfReferralError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"detail": "Não é possível usar o próprio código de indicação.", "code": "self_referral"},
    )


async def already_claimed_handler(request, exc: AlreadyClaimedError) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={"detail": "Esta conta já tem um indicador registrado.", "code": "already_claimed"},
    )


class LastRewardOut(BaseModel):
    """Última recompensa de indicação já aplicada ao próprio usuário (como indicador) -- tudo aqui
    vem direto da linha real em `referrals` (services.referrals.get_last_applied_reward), nunca
    calculado/inventado nesta rota."""

    reward_days: int
    is_milestone: bool
    plan_code: str | None = None  # só preenchido quando foi um marco (upgrade de plano)
    applied_at: datetime


class ReferralCodeOut(BaseModel):
    """O código -- o link completo (klango.site/?ref=CODIGO) é montado no frontend a partir da
    própria origem da página, sem precisar que o backend conheça/exponha a URL pública aqui.

    `last_reward` (04/10/2026, marco de indicações, aprovação do CÉREBRO): a última recompensa já
    aplicada a este usuário como indicador, se houver -- permite ao frontend mostrar um aviso de
    "sua indicação converteu" comparando `applied_at` com o que já foi mostrado antes (guardado no
    próprio navegador), sem o backend precisar saber o que já foi "visto"."""

    code: str
    last_reward: LastRewardOut | None = None


@router.get("/referral", response_model=ReferralCodeOut)
def get_referral_code(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> ReferralCodeOut:
    """Etapa 8 da revisão de UX (aprovação do CÉREBRO): devolve o código de indicação do próprio
    usuário, gerando um na primeira chamada (services.referrals.get_or_create_referral_code) --
    SOMENTE login de verdade, mesmo motivo de /generations (um código persistente não faz sentido
    pra um cookie de dispositivo sem conta). Também devolve a última recompensa já aplicada (se
    houver) -- ver LastRewardOut."""
    code = get_or_create_referral_code(db, user.id)
    reward = get_last_applied_reward(db, user.id)
    last_reward = None
    if reward is not None:
        is_milestone = reward.reward_days == REFERRAL_MILESTONE_REWARD_DAYS
        last_reward = LastRewardOut(
            reward_days=reward.reward_days,
            is_milestone=is_milestone,
            plan_code=REFERRAL_MILESTONE_PLAN_CODE if is_milestone else None,
            applied_at=reward.applied_at,
        )
    return ReferralCodeOut(code=code, last_reward=last_reward)


class ClaimReferralBody(BaseModel):
    code: str = Field(min_length=1, max_length=16)


class ClaimReferralOut(BaseModel):
    linked: bool = True


@router.post("/referral/claim", response_model=ClaimReferralOut)
def claim_referral_route(
    body: ClaimReferralBody,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> ClaimReferralOut:
    """Etapa 8 da revisão de UX (aprovação do CÉREBRO): o PRÓPRIO usuário recém-logado reivindica o
    código de quem o indicou -- chamado pelo frontend logo após o login, só quando um `?ref=` foi
    guardado antes do login (nunca antes disso: sem usuário autenticado não há em quem gravar
    `referred_by_user_id`). Idempotente na prática: se já tiver um indicador, levanta
    AlreadyClaimedError (409) -- o frontend trata isso como "nada a fazer", nunca como erro visível
    ao usuário (ver atualização de static/index.html)."""
    claim_referral(db, user_id=user.id, code=body.code)
    return ClaimReferralOut()
