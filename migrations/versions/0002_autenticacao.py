"""autenticação: login_codes e auth_sessions

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-20

Migration escrita à mão e congelada: NÃO importa nada dos modelos.
Não altera a tabela users nem a migration 0001.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "login_codes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("close_reason", sa.String(length=16), nullable=True),
        sa.Column("active_slot", sa.Integer(), nullable=True),
        sa.Column("ip_hash", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_login_codes"),
        sa.UniqueConstraint("email", "active_slot", name="uq_login_codes_email_active_slot"),
        sa.CheckConstraint("email = lower(trim(email))", name="ck_login_codes_email_normalizado"),
        sa.CheckConstraint("length(code_hash) = 64", name="ck_login_codes_code_hash_64"),
        sa.CheckConstraint("attempts >= 0", name="ck_login_codes_attempts_nao_negativo"),
        sa.CheckConstraint("expires_at > created_at", name="ck_login_codes_expires_at_maior_que_created_at"),
        sa.CheckConstraint("active_slot IS NULL OR active_slot = 1", name="ck_login_codes_active_slot_valido"),
        sa.CheckConstraint(
            "close_reason IS NULL OR close_reason IN ('verified', 'superseded', 'exhausted', 'send_failed')",
            name="ck_login_codes_close_reason_valido",
        ),
        sa.CheckConstraint(
            "(active_slot IS NOT NULL AND active_slot = 1 AND closed_at IS NULL AND close_reason IS NULL) "
            "OR (active_slot IS NULL AND closed_at IS NOT NULL AND close_reason IS NOT NULL)",
            name="ck_login_codes_aberto_ou_fechado",
        ),
    )
    op.create_index("ix_login_codes_email_created_at", "login_codes", ["email", "created_at"])
    op.create_index("ix_login_codes_ip_hash_created_at", "login_codes", ["ip_hash", "created_at"])

    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_auth_sessions"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_auth_sessions_user_id_users", ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("token_hash", name="uq_auth_sessions_token_hash"),
        sa.CheckConstraint("length(token_hash) = 64", name="ck_auth_sessions_token_hash_64"),
        sa.CheckConstraint("expires_at > created_at", name="ck_auth_sessions_expires_at_maior_que_created_at"),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_auth_sessions_user_id", table_name="auth_sessions")
    op.drop_table("auth_sessions")

    op.drop_index("ix_login_codes_ip_hash_created_at", table_name="login_codes")
    op.drop_index("ix_login_codes_email_created_at", table_name="login_codes")
    op.drop_table("login_codes")
