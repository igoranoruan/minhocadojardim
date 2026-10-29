"""Catálogo oficial de planos (em código, sem tabela).

Os planos pagos são compra única (sem recorrência). O Free não tem entitlement.

Cada vídeo processado consome 1 geração.

A cota de gerações é:
- Free: 5 gerações por semana.
- Semanal: 7 gerações por dia.
- Mensal: 10 gerações por dia.
- VIP Batch: 15 gerações por dia.

(Atualizado em 20/10/2026, 2ª rodada, pedido do Igor: a 1ª rodada do mesmo dia tinha subido o teto
diário de Semanal/Mensal para 10/15 -- empatando Mensal com o VIP Batch na cota diária. Igor
reconsiderou e pediu um teto mais conservador (Semanal 7, Mensal 10, de volta ao valor original),
restaurando a separação visível entre os 3 planos pagos: Semanal < Mensal < VIP Batch tanto na
cota diária quanto no teto de itens por lote. O limite de operações de batch POR SEMANA não mudou
em nenhuma das duas rodadas.)

Preços (reajustados em 22/10/2026, pedido e aprovação do Igor -- só valor/copy, NENHUM limite ou
regra de negócio mudou nesta rodada):
- Semanal: R$ 9,90 -> R$ 14,90 (price_cents 990 -> 1490)
- Mensal: R$ 16,90 -> R$ 27,90 (price_cents 1690 -> 2790)
- VIP Batch: R$ 29,90 -> R$ 47,90 (price_cents 2990 -> 4790)
- Free: sem mudança (sempre R$ 0,00).

payments/service.py::create_payment lê price_cents daqui e grava o valor no próprio Payment no
momento da criação (Payment.amount_cents) -- nunca recalculado depois. Então um Payment "pending"
criado ANTES deste reajuste (ex.: PIX gerado com o preço antigo, pago só depois do deploy) continua
cobrando e sendo conciliado pelo valor ANTIGO, congelado em Payment.amount_cents; a reconciliação
do webhook (payments/gateway.py::reconcile_webhook_payment) decide grant_entitlement só pelo status
do Mercado Pago e por payment.plan_code -- nunca recompara o valor pago contra o catálogo atual.
Confirmado em código antes deste reajuste (nenhuma mudança necessária para isso continuar valendo).

Batch possui duas dimensões independentes:
1. `max_batch_size`: quantidade máxima de vídeos em uma única operação.
2. `batch_limit`: quantidade máxima de operações de batch por período.

Regras de batch:
- Free: não pode usar batch.
- Semanal: 3 operações de batch por semana, até 7 vídeos por operação.
- Mensal: 5 operações de batch por semana, até 10 vídeos por operação.
- VIP Batch: operações ilimitadas, até 15 vídeos por operação.

`batch_limit=None` significa operações de batch ilimitadas.

Códigos usados em generations.plan_code, entitlements.plan_code e payments.plan_code:
free, weekly, monthly, vip_batch, trial_vip.

ETAPA B7 (teste VIP de 24h por link de campanha, pedido e aprovação do Igor, 29/10/2026): plano
`trial_vip` -- 10 vídeos, 1 lote de até 10, 5 extrações de áudio, TODOS num total único de 24h
(nunca por dia/semana de calendário, ver `period`/`batch_period` abaixo e services/usage.py e
services/audio_usage.py). NÃO vendável (`purchasable=False`) -- nunca aparece em checkout, nunca
pode ser comprado (ver payments/service.py::create_payment, que agora checa `purchasable`, não
mais `paid`).

`paid` (duration_days is not None) continua significando exatamente o que já significava: "este
plano tem uma janela de acesso e consome o bucket de cota de plano pago" -- é por isso que
`trial_vip.paid` é True (dá acesso de verdade a lote/áudio/atalho, via os mesmos pontos de
checagem que os planos comprados já usam) e PRECISA continuar sendo True para o teste funcionar.
`purchasable`, campo NOVO, é a resposta à pergunta DIFERENTE "isto é vendido/é receita?" -- True
para todo plano comprável de hoje (Semanal, Mensal, VIP Batch), False só para `trial_vip`. Nenhum
dos dois substitui o outro; cada um dos dois guards existentes (grant_entitlement/
grant_referral_milestone em services/entitlements.py, create_payment em payments/service.py)
passou a checar o que sempre devia ter checado."""

from dataclasses import dataclass
from typing import Literal


FREE = "free"
WEEKLY = "weekly"
MONTHLY = "monthly"
VIP_BATCH = "vip_batch"
TRIAL_VIP = "trial_vip"


@dataclass(frozen=True)
class Plan:
    code: str
    name: str
    price_cents: int  # dinheiro sempre em centavos (nunca float)
    duration_days: int | None  # None = Free (não é comprado)
    limit: int  # gerações por período
    period: Literal["week", "day", "trial"]  # Free: semana; pagos: dia; trial_vip: total de 24h
    max_batch_size: int = 0  # máximo de vídeos em uma operação de batch; 0 = sem batch
    batch_limit: int | None = 0  # operações de batch por período; None = ilimitado
    batch_period: Literal["week", "trial"] = "week"  # período da cota de operações de batch
    purchasable: bool = True  # False = nunca aparece em checkout, nunca pode ser comprado (trial_vip)

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
    # ETAPA B7 (29/10/2026): explícito -- Free nunca foi vendável (já era barrado por `paid=False`
    # em payments/service.py::create_payment, que agora checa `purchasable`; precisa continuar
    # barrado também por este campo, senão create_payment pararia de recusar "free").
    purchasable=False,
)

WEEKLY_PLAN = Plan(
    WEEKLY,
    "Semanal",
    1490,
    7,
    7,
    "day",
    max_batch_size=7,
    batch_limit=3,
    batch_period="week",
)

MONTHLY_PLAN = Plan(
    MONTHLY,
    "Mensal",
    2790,
    30,
    10,
    "day",
    max_batch_size=10,
    batch_limit=5,
    batch_period="week",
)

VIP_BATCH_PLAN = Plan(
    VIP_BATCH,
    "VIP Batch",
    4790,
    30,
    15,
    "day",
    max_batch_size=15,
    batch_limit=None,
    batch_period="week",
)


# ETAPA B7 (teste VIP de 24h, pedido e aprovação do Igor, 29/10/2026): 10 vídeos, 1 lote de até
# 10, 5 extrações de áudio -- TOTAIS únicos em 24h (period/batch_period="trial"), nunca por
# dia/semana de calendário (ver docstring do módulo). duration_days=1 (24h cabe exato num dia
# inteiro -- nenhum campo fracionário precisou ser inventado). price_cents=0 e purchasable=False:
# nunca vendável, nunca aparece em checkout (payments/service.py::create_payment recusa comprar
# qualquer plano com purchasable=False).
TRIAL_VIP_PLAN = Plan(
    TRIAL_VIP,
    "Teste VIP de 24h",
    0,
    1,
    10,
    "trial",
    max_batch_size=10,
    batch_limit=1,
    batch_period="trial",
    purchasable=False,
)


PLANS: dict[str, Plan] = {
    p.code: p
    for p in (
        FREE_PLAN,
        WEEKLY_PLAN,
        MONTHLY_PLAN,
        VIP_BATCH_PLAN,
        TRIAL_VIP_PLAN,
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
    """Planos que valem a pena mostrar/vender no checkout -- por isso filtra por `purchasable`
    (não por `paid`, ETAPA B7, 29/10/2026): `trial_vip` é `paid=True` (tem janela de acesso), mas
    `purchasable=False` (nunca vendável) -- continua de fora daqui, exatamente como antes desta
    etapa (o resultado desta função não mudou: [WEEKLY, MONTHLY, VIP_BATCH], mesma ordem)."""
    return tuple(p for p in PLANS.values() if p.purchasable)