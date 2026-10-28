"""cancela o Payment #30 (cartão, Mensal) travado em "pending" por reaproveitamento de idempotência

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-27

Migration de DADO, não de schema (nenhuma tabela/coluna/constraint muda). Autorização do Igor
(27/10/2026, investigação em conjunto do bug "Payment Brick de cartão não aprova"): o Payment #30
(user_id=1, plan_code="monthly", method="credit_card") foi submetido ao Mercado Pago e recebeu
mp_payment_id="185392782091", status="pending", status_detail="pending_waiting_transfer" (valor
atípico pra cartão -- típico de transferência/Pix, não de crédito).

Causa raiz identificada: payments/service.py::create_payment é idempotente por (user_id,
plan_code, method, status="pending") -- reaproveita esse MESMO Payment #30 em toda nova tentativa
de pagar o plano Mensal no cartão enquanto ele continuar "pending", e payments/gateway.py::charge
usa Payment.external_reference como X-Idempotency-Key em toda chamada a POST /v1/payments. Como
a chave nunca muda enquanto o Payment #30 for reaproveitado, o Mercado Pago devolve sempre a
MESMA resposta travada da primeira tentativa (a de pending_waiting_transfer), mesmo em tentativas
novas com token/cartão diferentes -- confirmado por auditoria completa do código (nenhum override
de payment_method_id, nenhum fallback para Pix/transferência em lugar nenhum do payload).

Esta migration cancela SÓ o Payment #30, SÓ se ele ainda estiver exatamente no estado descrito
acima (id, status e mp_payment_id conferidos no WHERE) -- idempotente: rodar de novo não faz nada
se já foi cancelado ou se o estado mudou por outro caminho (ex.: o webhook do Mercado Pago
reconciliou antes). Depois de cancelado, a próxima tentativa de pagar o Mensal no cartão passa
pelo "if existente is not None" de create_payment sem encontrar nada "pending" e cria um Payment
NOVO, com external_reference (e portanto X-Idempotency-Key) novo -- permitindo que o Mercado Pago
processe a tentativa como realmente nova.

Não mexe em nenhum outro Payment, em Mercado Pago, webhook, autenticação nem em limites de uso.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    payments = sa.table(
        "payments",
        sa.column("id", sa.Integer),
        sa.column("status", sa.String),
        sa.column("status_detail", sa.String),
        sa.column("mp_payment_id", sa.String),
    )
    conn.execute(
        payments.update()
        .where(payments.c.id == 30)
        .where(payments.c.status == "pending")
        .where(payments.c.mp_payment_id == "185392782091")
        .values(status="cancelled", status_detail="cancelled_travado_idempotencia_27_10_2026")
    )


def downgrade() -> None:
    # Deliberadamente sem downgrade -- mesmo raciocínio da 0014: reverter pra "pending" poderia
    # colidir com um Payment novo que o usuário já tenha criado depois (create_payment nunca
    # produz dois "pending" simultâneos para a mesma combinação user/plano/método em operação
    # normal), então recriar esse estado manualmente quebraria essa garantia.
    pass
