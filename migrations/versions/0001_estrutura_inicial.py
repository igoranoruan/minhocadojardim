"""estrutura inicial: users, payments, entitlements, batches, generations, payment_events

Revision ID: 0001
Revises:
Create Date: 2026-09-20

Migration escrita à mão e congelada: NÃO importa nada dos modelos, para que
mudanças futuras nos modelos não alterem o que esta migration faz.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("email", name="uq_users_email"),
        sa.CheckConstraint("email = lower(trim(email))", name="ck_users_email_normalizado"),
    )

    op.create_table(
        "payments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("plan_code", sa.String(length=32), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("method", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("mp_payment_id", sa.String(length=64), nullable=True),
        sa.Column("external_reference", sa.String(length=64), nullable=False),
        sa.Column("status_detail", sa.String(length=64), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("refunded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_payments"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_payments_user_id_users", ondelete="RESTRICT"),
        sa.UniqueConstraint("mp_payment_id", name="uq_payments_mp_payment_id"),
        sa.UniqueConstraint("external_reference", name="uq_payments_external_reference"),
        sa.CheckConstraint("amount_cents > 0", name="ck_payments_amount_cents_positivo"),
        sa.CheckConstraint("method IN ('pix', 'credit_card')", name="ck_payments_method_valido"),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'cancelled', 'refunded', 'charged_back')",
            name="ck_payments_status_valido",
        ),
    )
    op.create_index("ix_payments_user_id_created_at", "payments", ["user_id", "created_at"])

    op.create_table(
        "entitlements",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("plan_code", sa.String(length=32), nullable=False),
        sa.Column("payment_id", sa.Integer(), nullable=False),
        sa.Column("duration_days", sa.Integer(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoke_reason", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_entitlements"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_entitlements_user_id_users", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["payment_id"], ["payments.id"], name="fk_entitlements_payment_id_payments", ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("payment_id", name="uq_entitlements_payment_id"),
        sa.CheckConstraint("duration_days > 0", name="ck_entitlements_duration_days_positivo"),
        sa.CheckConstraint("expires_at > starts_at", name="ck_entitlements_expires_at_maior_que_starts_at"),
        sa.CheckConstraint("status IN ('granted', 'revoked')", name="ck_entitlements_status_valido"),
        sa.CheckConstraint(
            "(status = 'granted' AND revoked_at IS NULL) OR (status = 'revoked' AND revoked_at IS NOT NULL)",
            name="ck_entitlements_revogacao_consistente",
        ),
    )
    op.create_index(
        "ix_entitlements_user_id_status_expires_at", "entitlements", ["user_id", "status", "expires_at"]
    )

    op.create_table(
        "batches",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_batches"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_batches_user_id_users", ondelete="RESTRICT"),
        sa.UniqueConstraint("user_id", "request_id", name="uq_batches_user_id_request_id"),
        sa.CheckConstraint("item_count >= 1", name="ck_batches_item_count_minimo"),
    )

    op.create_table(
        "generations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("batch_id", sa.Integer(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("plan_code", sa.String(length=32), nullable=False),
        sa.Column("entitlement_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("period_day", sa.Date(), nullable=False),
        sa.Column("period_week", sa.Date(), nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("output_sha256", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_generations"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_generations_user_id_users", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["batch_id"], ["batches.id"], name="fk_generations_batch_id_batches", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["entitlement_id"],
            ["entitlements.id"],
            name="fk_generations_entitlement_id_entitlements",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("user_id", "request_id", name="uq_generations_user_id_request_id"),
        sa.UniqueConstraint("batch_id", "position", name="uq_generations_batch_id_position"),
        sa.CheckConstraint("status IN ('reserved', 'completed', 'failed')", name="ck_generations_status_valido"),
        sa.CheckConstraint(
            "(batch_id IS NULL AND position IS NULL AND request_id IS NOT NULL) "
            "OR (batch_id IS NOT NULL AND position IS NOT NULL AND request_id IS NULL)",
            name="ck_generations_avulsa_ou_lote",
        ),
        sa.CheckConstraint("position IS NULL OR position >= 1", name="ck_generations_position_minimo"),
        sa.CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0", name="ck_generations_duration_ms_nao_negativo"
        ),
        sa.CheckConstraint(
            "output_sha256 IS NULL OR length(output_sha256) = 64", name="ck_generations_output_sha256_64"
        ),
    )
    op.create_index(
        "ix_generations_user_id_period_week_plan_code_status",
        "generations",
        ["user_id", "period_week", "plan_code", "status"],
    )
    op.create_index(
        "ix_generations_user_id_period_day_plan_code_status",
        "generations",
        ["user_id", "period_day", "plan_code", "status"],
    )

    op.create_table(
        "payment_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=True),
        sa.Column("resource_id", sa.String(length=64), nullable=True),
        sa.Column("payload", sa.Text(), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result", sa.String(length=32), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_payment_events"),
    )
    op.create_index("ix_payment_events_provider_resource_id", "payment_events", ["provider", "resource_id"])
    op.create_index("ix_payment_events_received_at", "payment_events", ["received_at"])


def downgrade() -> None:
    # Ordem inversa da criação (filhos antes dos pais), por causa das foreign keys.
    op.drop_index("ix_payment_events_received_at", table_name="payment_events")
    op.drop_index("ix_payment_events_provider_resource_id", table_name="payment_events")
    op.drop_table("payment_events")

    op.drop_index("ix_generations_user_id_period_day_plan_code_status", table_name="generations")
    op.drop_index("ix_generations_user_id_period_week_plan_code_status", table_name="generations")
    op.drop_table("generations")

    op.drop_table("batches")

    op.drop_index("ix_entitlements_user_id_status_expires_at", table_name="entitlements")
    op.drop_table("entitlements")

    op.drop_index("ix_payments_user_id_created_at", table_name="payments")
    op.drop_table("payments")

    op.drop_table("users")
