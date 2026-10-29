"""teste VIP de 24h: entitlements.payment_id opcional (só para trial_vip) + tabela trial_redemptions

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-29

Migration escrita à mão e congelada: NÃO importa nada dos modelos. Não altera 0001-0016.

ETAPA B7 (teste VIP de 24h por link de campanha, pedido do Igor, 29/10/2026 -- proposta aprovada
na mesma data, opção A). Duas mudanças:

1) `entitlements.payment_id` passa de NOT NULL para NULLABLE, e ganha um novo CHECK
   (`ck_entitlements_payment_id_so_para_planos_pagos`) que trava a única combinação permitida:
   payment_id é NULL quando (e só quando) plan_code = 'trial_vip'. Todo entitlement de QUALQUER
   outro plano continua exigindo payment_id -- nenhum relaxamento além do estritamente necessário
   para o teste. Nenhuma coluna de `payments` é tocada; nenhuma migração 0001-0016 é alterada.

2) Tabela nova `trial_redemptions`: ledger do resgate (user_id, campanha, hash de IP, hash de
   aparelho) para as travas de abuso e para a medição por campanha (seção 5 do pedido, etapa
   seguinte) -- nunca uma segunda fonte de acesso (quem decide acesso continua sendo só
   `entitlements`, ver database/models/entitlement.py).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("entitlements", schema=None) as batch_op:
        batch_op.alter_column("payment_id", existing_type=sa.Integer(), nullable=True)
        batch_op.create_check_constraint(
            "ck_entitlements_payment_id_so_para_planos_pagos",
            "(plan_code = 'trial_vip' AND payment_id IS NULL) "
            "OR (plan_code != 'trial_vip' AND payment_id IS NOT NULL)",
        )

    op.create_table(
        "trial_redemptions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("entitlement_id", sa.Integer(), nullable=False),
        sa.Column("campaign", sa.String(length=40), nullable=False),
        sa.Column("ip_hash", sa.String(length=64), nullable=True),
        sa.Column("device_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_trial_redemptions"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_trial_redemptions_user_id_users", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["entitlement_id"], ["entitlements.id"],
            name="fk_trial_redemptions_entitlement_id_entitlements", ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("user_id", name="uq_trial_redemptions_user_id"),
        sa.UniqueConstraint("entitlement_id", name="uq_trial_redemptions_entitlement_id"),
        sa.CheckConstraint("campaign != ''", name="ck_trial_redemptions_campaign_nao_vazia"),
    )
    op.create_index("ix_trial_redemptions_device_hash", "trial_redemptions", ["device_hash"])
    op.create_index(
        "ix_trial_redemptions_ip_hash_created_at", "trial_redemptions", ["ip_hash", "created_at"]
    )
    op.create_index("ix_trial_redemptions_campaign", "trial_redemptions", ["campaign"])


def downgrade() -> None:
    op.drop_index("ix_trial_redemptions_campaign", table_name="trial_redemptions")
    op.drop_index("ix_trial_redemptions_ip_hash_created_at", table_name="trial_redemptions")
    op.drop_index("ix_trial_redemptions_device_hash", table_name="trial_redemptions")
    op.drop_table("trial_redemptions")

    with op.batch_alter_table("entitlements", schema=None) as batch_op:
        batch_op.drop_constraint("ck_entitlements_payment_id_so_para_planos_pagos", type_="check")
        batch_op.alter_column("payment_id", existing_type=sa.Integer(), nullable=False)
