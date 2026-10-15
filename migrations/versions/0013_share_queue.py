"""atalho "Enviar para o KLANGO": share_tokens e share_queue_items

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-15

Migration escrita à mão e congelada: NÃO importa nada dos modelos. Não altera 0001-0012.

Autorização do CÉREBRO (15/10/2026, atalho de compartilhamento Instagram/TikTok/Pinterest/
YouTube -> fila no KLANGO): CRIA duas tabelas NOVAS, totalmente separadas de
`generations`/`batches` -- a fila nunca consome geração nem conta na cota de lote dos planos
pagos; o download continua acontecendo só pelo /api/download-batch já existente, quando o usuário
confirma o envio (ver database/models/share_queue_item.py e services/share_queue.py).

`share_tokens` guarda só o SHA-256 do token pessoal do atalho (mesmo padrão de `auth_sessions`),
com a invariável "no máximo 1 token ativo por usuário" garantida por UNIQUE(user_id, active_slot) --
MESMO idioma já usado em `login_codes.active_slot` (ver 0002_autenticacao.py): NULL nunca colide
com NULL, então pode haver vários tokens revogados, mas só um ativo por usuário.

`share_queue_items` guarda os links já validados/normalizados esperando serem enviados a um lote.
Sem FK para generations/batches -- um item nunca aponta para uma geração específica, só vira uma
URL na lista que o usuário confirma manualmente no lote normal.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "share_tokens",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("active_slot", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_share_tokens"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_share_tokens_user_id_users", ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("token_hash", name="uq_share_tokens_token_hash"),
        sa.UniqueConstraint("user_id", "active_slot", name="uq_share_tokens_user_id_active_slot"),
        sa.CheckConstraint("length(token_hash) = 64", name="ck_share_tokens_token_hash_64"),
        sa.CheckConstraint("active_slot IS NULL OR active_slot = 1", name="ck_share_tokens_active_slot_valido"),
        sa.CheckConstraint(
            "(active_slot IS NOT NULL AND active_slot = 1 AND revoked_at IS NULL) "
            "OR (active_slot IS NULL AND revoked_at IS NOT NULL)",
            name="ck_share_tokens_ativo_ou_revogado",
        ),
    )
    op.create_index("ix_share_tokens_user_id", "share_tokens", ["user_id"])

    op.create_table(
        "share_queue_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_share_queue_items"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_share_queue_items_user_id_users", ondelete="RESTRICT"
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'consumed', 'removed')", name="ck_share_queue_items_status_valido"
        ),
    )
    op.create_index(
        "ix_share_queue_items_user_id_status", "share_queue_items", ["user_id", "status"]
    )


def downgrade() -> None:
    op.drop_index("ix_share_queue_items_user_id_status", table_name="share_queue_items")
    op.drop_table("share_queue_items")
    op.drop_index("ix_share_tokens_user_id", table_name="share_tokens")
    op.drop_table("share_tokens")
