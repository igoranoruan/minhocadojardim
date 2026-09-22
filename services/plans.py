"""Catálogo oficial de planos (em código, sem tabela).

Os planos pagos são compra única (sem recorrência). O Free não tem entitlement.
Nenhum plano é ilimitado. Cada vídeo processado consome 1 geração.

Batch usa a MESMA cota de gerações do plano (não existe quota separada para lote):
o teto de vídeos por lote é `max_batch_size`, mas o consumo real é sempre limitado também
pelo saldo de gerações disponível no momento (ver services/usage.reserve_batch).

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
    period: Literal["week", "day"]  # Free: semana (segunda a domingo, SP). Pagos: dia (SP)
    max_batch_size: int = 0  # teto de vídeos por lote deste plano; 0 = não pode usar lote

    @property
    def paid(self) -> bool:
        return self.duration_days is not None

    @property
    def batch_enabled(self) -> bool:
        return self.max_batch_size > 0


FREE_PLAN = Plan(FREE, "Free", 0, None, 5, "week")
WEEKLY_PLAN = Plan(WEEKLY, "Semanal", 990, 7, 5, "day", max_batch_size=5)
MONTHLY_PLAN = Plan(MONTHLY, "Mensal", 1690, 30, 10, "day", max_batch_size=10)
VIP_BATCH_PLAN = Plan(VIP_BATCH, "VIP Batch", 2990, 30, 15, "day", max_batch_size=15)

PLANS: dict[str, Plan] = {p.code: p for p in (FREE_PLAN, WEEKLY_PLAN, MONTHLY_PLAN, VIP_BATCH_PLAN)}


class UnknownPlanError(ValueError):
    """plan_code que não existe no catálogo (dado inconsistente: nunca cair no Free em silêncio)."""


def get_plan(code: str) -> Plan:
    try:
        return PLANS[code]
    except KeyError:
        raise UnknownPlanError(f"Plano desconhecido: {code!r}") from None


def paid_plans() -> tuple[Plan, ...]:
    return tuple(p for p in PLANS.values() if p.paid)
