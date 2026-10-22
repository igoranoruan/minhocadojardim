"""cancela Payments "pending" com o preço antigo (pré-reajuste de 22/10/2026)

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-22

Migration de DADO, não de schema (nenhuma tabela/coluna/constraint muda). Autorização do Igor
(22/10/2026): depois do reajuste de preço dos planos (services/plans.py, commit 93380b8), o
modal de pagamento (PIX/Cartão) continuava mostrando o preço ANTIGO para contas que já tinham um
Payment "pending" aberto de antes do reajuste -- causa raiz: payments/service.py::create_payment
é idempotente por (user_id, plan_code, method, status="pending") e reaproveita esse registro
antigo em vez de criar um novo, então o amount_cents congelado na criação (o preço de quando o
Payment foi aberto) nunca é atualizado.

Esta migration cancela só os 13 Payments "pending" confirmados (consulta direta ao banco de
produção, 22/10/2026) que: (a) ainda estão "pending", (b) NUNCA chegaram a ser cobrados de
verdade (mp_payment_id IS NULL -- nenhuma tentativa de PIX/cartão foi confirmada pelo Mercado
Pago para eles) e (c) têm amount_cents diferente do price_cents atual do próprio plan_code
(prova de que são de antes do reajuste). Depois de cancelados, o próximo clique nesses
planos/métodos passa pelo "if existente is not None" de create_payment sem encontrar nada
"pending" e cria um Payment NOVO, já com o preço atual.

Não mexe em Mercado Pago, webhook, autenticação, limites de uso nem em nenhum Payment que já foi
cobrado (mp_payment_id preenchido) ou que já reflita o preço atual -- essa 2ª condição (amount_cents
!= price_cents atual do plano) também torna a migration idempotente: rodar de novo (ex.: reaplicar
em outro ambiente) não cancela nada que já esteja com o preço certo, e um Payment já cancelado não
é "pending" então sai do WHERE sozinho.

IDs confirmados nesta consulta (todos plan_code/method/amount_cents antigos, mp_payment_id nulo):
2, 3, 6, 7, 12, 14, 15, 16, 18, 19, 21, 22, 23.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_IDS_CONFIRMADOS = (2, 3, 6, 7, 12, 14, 15, 16, 18, 19, 21, 22, 23)

# price_cents ATUAL do catálogo (services/plans.py, reajuste de 22/10/2026) -- duplicado aqui de
# propósito: migrations são congeladas no tempo (mesmo espírito do comentário em
# 0013_share_queue.py, "NÃO importa nada dos modelos"), nunca devem importar services/plans.py,
# que pode mudar de novo no futuro.
_PRECO_ATUAL_POR_PLANO = {"weekly": 1490, "monthly": 2790, "vip_batch": 4790}


def upgrade() -> None:
    conn = op.get_bind()
    payments = sa.table(
        "payments",
        sa.column("id", sa.Integer),
        sa.column("plan_code", sa.String),
        sa.column("amount_cents", sa.Integer),
        sa.column("status", sa.String),
        sa.column("status_detail", sa.String),
        sa.column("mp_payment_id", sa.String),
    )
    for plano, preco_atual in _PRECO_ATUAL_POR_PLANO.items():
        conn.execute(
            payments.update()
            .where(payments.c.id.in_(_IDS_CONFIRMADOS))
            .where(payments.c.plan_code == plano)
            .where(payments.c.status == "pending")
            .where(payments.c.mp_payment_id.is_(None))
            .where(payments.c.amount_cents != preco_atual)
            .values(status="cancelled", status_detail="cancelled_preco_desatualizado_reajuste_22_10_2026")
        )


def downgrade() -> None:
    # Deliberadamente sem downgrade: não há como recuperar com segurança o status "pending"
    # original -- um dos Payments cancelados aqui pode, por coincidência, já ter sido substituído
    # por um Payment novo do mesmo usuário/plano/método (create_payment cria um assim que o
    # anterior sai de "pending"); reverter para "pending" de novo criaria DOIS "pending"
    # simultâneos para a mesma combinação, estado que create_payment nunca produz na operação
    # normal e que quebraria a idempotência dele.
    pass
