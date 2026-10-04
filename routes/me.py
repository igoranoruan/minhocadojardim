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
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database.models import User
from database.session import get_session
from routes.deps import get_current_user, get_generation_user
from services.generation_history import get_generation_history
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
