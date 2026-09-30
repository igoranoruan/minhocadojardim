"""tabela anonymous_identities (Free anônimo, sem login -- aprovação do CÉREBRO)

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-30

Migration escrita à mão e congelada: NÃO importa nada dos modelos. Não altera 0001/0002/0003/0004/0005.

Cria SOMENTE a tabela anonymous_identities -- mapeamento cookie (hash) -> user_id de um usuário
"placeholder" (linha real em `users`, criada automaticamente, e-mail sintético @device.invalid,
nunca verificado). NENHUMA coluna de `users` ou `generations` é alterada: user_id continua NOT
NULL em generations, email continua NOT NULL/único em users -- exatamente como aprovado. Isso
permite que TODA a cadeia existente de cota/ownership (services/usage.py, services/entitlements.py,
services/locks.py, download por generation.user_id) funcione sem duplicação para o visitante
anônimo: ele é, para o banco, um usuário real como qualquer outro.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "anonymous_identities",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("device_token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_anonymous_identities"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_anonymous_identities_user_id_users", ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("user_id", name="uq_anonymous_identities_user_id"),
        sa.UniqueConstraint("device_token_hash", name="uq_anonymous_identities_device_token_hash"),
    )


def downgrade() -> None:
    op.drop_table("anonymous_identities")
