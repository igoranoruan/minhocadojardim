"""cancela o Payment #31 (cartão, Mensal) travado em "pending" por reaproveitamento de idempotência

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-28

Migration de DADO, não de schema (nenhuma tabela/coluna/constraint muda). Autorização do Igor
(28/10/2026, continuação da investigação do bug "Payment Brick de cartão não aprova" no
Safari/iOS): o Payment #31 (user_id=1, plan_code="monthly", method="credit_card") foi submetido ao
Mercado Pago e recebeu mp_payment_id="185438200423", status="pending",
status_detail="pending_waiting_transfer" -- exatamente o mesmo padrão atípico já visto no Payment
#30 (ver migration 0015).

Confirmado pelos logs de diagnóstico temporário (payments/client.py::create_payment, ver commit
ee9ec00): numa tentativa de pagamento SEGUINTE a essa, o payload que de fato saímos enviando ao
Mercado Pago tinha payment_method_id="master" (cartão real, corretamente detectado pelo Payment
Brick) -- mas a resposta voltou com payment_method_id="pix"/payment_type_id="bank_transfer",
idêntica à primeira resposta travada do Payment #31 (mesmo mp_payment_id, mesmo
status_detail, updated_at do Payment não mudou). Ou seja: aquela tentativa nunca foi processada
de verdade pelo Mercado Pago -- devolveu a resposta CONGELADA da primeira vez, porque
payments/service.py::create_payment reaproveitou o Payment #31 (ainda "pending") e, com isso,
reenviou a mesma X-Idempotency-Key (Payment.external_reference) de antes. Mesma causa raiz já
identificada e documentada na migration 0015.

Esta migration cancela SÓ o Payment #31, SÓ se ele ainda estiver exatamente no estado descrito
acima (id, status e mp_payment_id conferidos no WHERE) -- idempotente: rodar de novo não faz nada
se já foi cancelado ou se o estado mudou por outro caminho (ex.: o webhook do Mercado Pago
reconciliou antes). Depois de cancelado, a próxima tentativa de pagar o Mensal no cartão passa
pelo "if existente is not None" de create_payment sem encontrar nada "pending" e cria um Payment
NOVO, com external_reference (e portanto X-Idempotency-Key) novo -- permitindo que o Mercado Pago
processe a tentativa como realmente nova.

Não mexe em nenhum outro Payment, em Mercado Pago, webhook, autenticação nem em limites de uso.
Não resolve, sozinha, o bug separado de validação "Dado obrigatório" no Payment Brick em
Safari/iOS (ainda em investigação com o suporte do Mercado Pago) -- só destrava o Payment
congelado, pra que a próxima tentativa possa ser avaliada de verdade pelo Mercado Pago.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0016"
down_revision: Union[str, None] = "0015"
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
        .where(payments.c.id == 31)
        .where(payments.c.status == "pending")
        .where(payments.c.mp_payment_id == "185438200423")
        .values(status="cancelled", status_detail="cancelled_travado_idempotencia_28_10_2026")
    )


def downgrade() -> None:
    # Deliberadamente sem downgrade -- mesmo raciocínio da 0014/0015: reverter pra "pending"
    # poderia colidir com um Payment novo que o usuário já tenha criado depois (create_payment
    # nunca produz dois "pending" simultâneos para a mesma combinação user/plano/método em
    # operação normal), então recriar esse estado manualmente quebraria essa garantia.
    pass
