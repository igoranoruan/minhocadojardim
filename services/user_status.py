"""Camada fina de composição para o status do usuário (UX-1).

Fluxo:

    routes/me.py -> services/user_status.py -> services.usage + services.entitlements

Este módulo NÃO cria nenhuma regra de negócio nova: só delega para os dois serviços já
existentes e já testados. Toda decisão sobre plano, limite, período, uso e validade continua
exclusivamente em services/usage.py (Allowance) e services/entitlements.py
(get_current_entitlement) -- aqui só reunimos o que routes/me.py precisa em uma única chamada.

Motivo de existir (Etapa de correção arquitetural do UX-1): tests/test_usage_architecture.py ::
test_nenhuma_regra_de_cota_em_rotas_main_ou_frontend proíbe qualquer rota (fora da lista nomeada
USAGE_ALLOWED_CONSUMERS, à qual routes.me não foi adicionada) de importar services.usage ou
services.entitlements diretamente. Este módulo não está em BUSINESS (services/plans.py,
services/entitlement_chain.py, services/entitlements.py, services/locks.py, services/usage.py) --
ele é só uma camada de composição/tradução, sem lógica de cota ou de validade própria -- então
routes/me.py pode importar `services.user_status` sem violar essa proteção.
"""
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from services.entitlements import get_current_entitlement
from services.plans import Plan
from services.usage import get_allowance


@dataclass(frozen=True)
class UserStatus:
    """Estrutura mínima de repasse para routes/me.py -- não duplica Allowance nem Entitlement,
    só reúne os poucos campos que o contrato HTTP do UX-1 precisa. `plan` é o Plan de
    Allowance.plan (mesma fonte autoritativa de sempre); `expires_at` vem de
    Entitlement.expires_at quando há entitlement vigente, ou None (Free/expirado/revoked)."""

    plan: Plan
    used: int
    limit: int
    remaining: int
    expires_at: datetime | None


def get_user_status(db: Session, user_id: int) -> UserStatus:
    """Compõe get_allowance(db, user_id) + get_current_entitlement(db, user_id) -- nenhuma
    decisão nova aqui, só delegação e montagem do resultado."""
    allowance = get_allowance(db, user_id)

    expires_at = None
    if allowance.entitlement_id is not None:
        entitlement = get_current_entitlement(db, user_id)
        if entitlement is not None:
            expires_at = entitlement.expires_at

    return UserStatus(
        plan=allowance.plan,
        used=allowance.used,
        limit=allowance.limit,
        remaining=allowance.remaining,
        expires_at=expires_at,
    )