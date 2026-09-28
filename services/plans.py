"""Catálogo oficial de planos (em código, sem tabela).

Os planos pagos são compra única (sem recorrência). O Free não tem entitlement.

Cada vídeo processado consome 1 geração.

A cota de gerações é:
- Free: 5 gerações por semana.
- Semanal: 5 gerações por dia.
- Mensal: 10 gerações por dia.
- VIP Batch: 15 gerações por dia.

Batch possui duas dimensões independentes:
1. `max_batch_size`: quantidade máxima de vídeos em uma única operação.
2. `batch_limit`: quantidade máxima de operações de batch por período.

Regras de batch:
- Free: não pode usar batch.
- Semanal: 3 operações de batch por semana, até 5 vídeos por operação.
- Mensal: 5 operações de batch por semana, até 5 vídeos por operação.
- VIP Batch: operações ilimitadas, até 15 vídeos por operação.

`batch_limit=None` significa operações de batch ilimitadas.

Códigos usados em generations.plan_code, entitlements.plan_code e payments.plan_code:
free, weekly, monthly, vip_batch.
"""

from dataclasses import dataclass
from typing import Literal


FREE = "free"
WEEKLY = "weekly"
MONTHLY = "monthly"
VIP_BATCH = "vip_batch"


@dataclass(frozen=True)
class Plan:
    code: str
    name: str
    price_cents: int  # dinheiro sempre em centavos (nunca float)
    duration_days: int | None  # None = Free (não é comprado)
    limit: int  # gerações por período
    period: Literal["week", "day"]  # Free: semana; pagos: dia
    max_batch_size: int = 0  # máximo de vídeos em uma operação de batch; 0 = sem batch
    batch_limit: int | None = 0  # operações de batch por período; None = ilimitado
    batch_period: Literal["week"] = "week"  # período da cota de operações de batch

    @property
    def paid(self) -> bool:
        return self.duration_days is not None

    @property
    def batch_enabled(self) -> bool:
        return self.max_batch_size > 0


FREE_PLAN = Plan(
    FREE,
    "Free",
    0,
    None,
    5,
    "week",
    max_batch_size=0,
    batch_limit=0,
)

WEEKLY_PLAN = Plan(
    WEEKLY,
    "Semanal",
    990,
    7,
    5,
    "day",
    max_batch_size=5,
    batch_limit=3,
    batch_period="week",
)

MONTHLY_PLAN = Plan(
    MONTHLY,
    "Mensal",
    1690,
    30,
    10,
    "day",
    max_batch_size=5,
    batch_limit=5,
    batch_period="week",
)

VIP_BATCH_PLAN = Plan(
    VIP_BATCH,
    "VIP Batch",
    2990,
    30,
    15,
    "day",
    max_batch_size=15,
    batch_limit=None,
    batch_period="week",
)


PLANS: dict[str, Plan] = {
    p.code: p
    for p in (
        FREE_PLAN,
        WEEKLY_PLAN,
        MONTHLY_PLAN,
        VIP_BATCH_PLAN,
    )
}


class UnknownPlanError(ValueError):
    """plan_code que não existe no catálogo (dado inconsistente: nunca cair no Free em silêncio)."""


def get_plan(code: str) -> Plan:
    try:
        return PLANS[code]
    except KeyError:
        raise UnknownPlanError(f"Plano desconhecido: {code!r}") from None


def paid_plans() -> tuple[Plan, ...]:
    return tuple(p for p in PLANS.values() if p.paid)