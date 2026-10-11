"""extração de áudio: audio_batches e audio_extractions

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-11

Migration escrita à mão e congelada: NÃO importa nada dos modelos. Não altera 0001-0011.

Autorização do CÉREBRO (11/10/2026, extração de áudio individual + lote): CRIA duas tabelas NOVAS,
totalmente separadas de `generations`/`batches` -- a extração de áudio nunca consome geração nem
conta na cota semanal de operações de lote dos planos pagos (ver database/models/audio_extraction.py
e database/models/audio_batch.py para a justificativa completa de manter tabelas próprias em vez
de reaproveitar/alterar as já existentes).

`audio_batches` é criada ANTES de `audio_extractions` nesta mesma migration porque
`audio_extractions.batch_id` referencia `audio_batches.id` -- mesma ordem já usada em 0001 para
`batches`/`generations`. O schema completo (incluindo batch_id/position) já nasce pronto aqui,
mesmo a Parte B (lote) sendo implementada depois, em cima deste MESMO schema (evita uma segunda
migration só para adicionar colunas de FK a uma tabela que a Parte A já criou).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "audio_batches",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_audio_batches"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_audio_batches_user_id_users", ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("user_id", "request_id", name="uq_audio_batches_user_id_request_id"),
        sa.CheckConstraint("item_count >= 1", name="ck_audio_batches_item_count_minimo"),
    )

    op.create_table(
        "audio_extractions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("batch_id", sa.Integer(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("period_day", sa.Date(), nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("output_sha256", sa.String(length=64), nullable=True),
        sa.Column("output_size_bytes", sa.Integer(), nullable=True),
        sa.Column("output_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("output_storage_key", sa.String(length=255), nullable=True),
        sa.Column("display_filename", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_audio_extractions"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_audio_extractions_user_id_users", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["audio_batches.id"],
            name="fk_audio_extractions_batch_id_audio_batches",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("user_id", "request_id", name="uq_audio_extractions_user_id_request_id"),
        sa.UniqueConstraint("batch_id", "position", name="uq_audio_extractions_batch_id_position"),
        sa.CheckConstraint(
            "status IN ('reserved', 'completed', 'failed')", name="ck_audio_extractions_status_valido"
        ),
        sa.CheckConstraint(
            "(batch_id IS NULL AND position IS NULL AND request_id IS NOT NULL) "
            "OR (batch_id IS NOT NULL AND position IS NOT NULL AND request_id IS NULL)",
            name="ck_audio_extractions_avulsa_ou_lote",
        ),
        sa.CheckConstraint("position IS NULL OR position >= 1", name="ck_audio_extractions_position_minimo"),
        sa.CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0", name="ck_audio_extractions_duration_ms_nao_negativo"
        ),
        sa.CheckConstraint(
            "output_sha256 IS NULL OR length(output_sha256) = 64", name="ck_audio_extractions_output_sha256_64"
        ),
        sa.CheckConstraint(
            "output_size_bytes IS NULL OR output_size_bytes >= 0",
            name="ck_audio_extractions_output_size_bytes_nao_negativo",
        ),
    )
    op.create_index(
        "ix_audio_extractions_user_id_period_day_status",
        "audio_extractions",
        ["user_id", "period_day", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_audio_extractions_user_id_period_day_status", table_name="audio_extractions")
    op.drop_table("audio_extractions")
    op.drop_table("audio_batches")
