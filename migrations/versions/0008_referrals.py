"""programa de indicação: users.referral_code / users.referred_by_user_id + tabela referrals

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-03

Migration escrita à mão e congelada: NÃO importa nada dos modelos. Não altera 0001-0007.

Duas colunas novas em `users` (ambas NULLABLE, sem backfill -- toda conta já existente, inclusive
toda identidade Free anônima, simplesmente nunca teve um código ou uma indicação, o que já É o
significado de NULL aqui):
- referral_code: código curto e único, gerado SOB DEMANDA (nunca em massa por esta migration) na
  primeira vez que o próprio usuário pede seu link de indicação.
- referred_by_user_id: autorreferência para outro `users.id` -- quem indicou este usuário, setado
  uma única vez pelo próprio usuário depois do login. CHECK garante que ninguém referencia a si
  mesmo.

Tabela nova `referrals`: o livro-razão de indicações RECOMPENSADAS (uma linha por indicação paga,
nunca por clique/cadastro) -- ver database/models/referral.py para o contrato completo.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(sa.Column("referral_code", sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column("referred_by_user_id", sa.Integer(), nullable=True))
        batch_op.create_unique_constraint("uq_users_referral_code", ["referral_code"])
        batch_op.create_check_constraint(
            "ck_users_nao_auto_indicacao",
            "referred_by_user_id IS NULL OR referred_by_user_id != id",
        )
        batch_op.create_foreign_key(
            "fk_users_referred_by_user_id_users",
            "users",
            ["referred_by_user_id"], ["id"],
            ondelete="RESTRICT",
        )

    op.create_table(
        "referrals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("referrer_user_id", sa.Integer(), nullable=False),
        sa.Column("referred_user_id", sa.Integer(), nullable=False),
        sa.Column("reward_days", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("applied_entitlement_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_referrals"),
        sa.ForeignKeyConstraint(
            ["referrer_user_id"], ["users.id"], name="fk_referrals_referrer_user_id_users", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["referred_user_id"], ["users.id"], name="fk_referrals_referred_user_id_users", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["applied_entitlement_id"], ["entitlements.id"],
            name="fk_referrals_applied_entitlement_id_entitlements", ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("referred_user_id", name="uq_referrals_referred_user_id"),
        sa.CheckConstraint("reward_days > 0", name="ck_referrals_reward_days_positivo"),
        sa.CheckConstraint("status IN ('pending_plan', 'applied')", name="ck_referrals_status_valido"),
        sa.CheckConstraint(
            "(status = 'pending_plan' AND applied_at IS NULL AND applied_entitlement_id IS NULL) "
            "OR (status = 'applied' AND applied_at IS NOT NULL AND applied_entitlement_id IS NOT NULL)",
            name="ck_referrals_aplicacao_consistente",
        ),
        sa.CheckConstraint("referrer_user_id != referred_user_id", name="ck_referrals_nao_autoindicacao"),
    )
    op.create_index("ix_referrals_referrer_user_id_status", "referrals", ["referrer_user_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_referrals_referrer_user_id_status", table_name="referrals")
    op.drop_table("referrals")

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_constraint("fk_users_referred_by_user_id_users", type_="foreignkey")
        batch_op.drop_constraint("ck_users_nao_auto_indicacao", type_="check")
        batch_op.drop_constraint("uq_users_referral_code", type_="unique")
        batch_op.drop_column("referred_by_user_id")
        batch_op.drop_column("referral_code")
