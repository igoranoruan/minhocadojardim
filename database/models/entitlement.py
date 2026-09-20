from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base, sql_in
from database.types import UTCDateTime, utcnow

ENTITLEMENT_STATUSES = ("granted", "revoked")


class Entitlement(Base):
    """Período de acesso pago. Um pagamento gera no máximo um acesso (payment_id único).

    Esta etapa só cria a persistência: empilhamento, realinhamento, revogação e o
    cálculo do acesso vigente são regras de serviço de etapas posteriores.
    """

    __tablename__ = "entitlements"
    __table_args__ = (
        UniqueConstraint("payment_id", name="uq_entitlements_payment_id"),
        CheckConstraint("duration_days > 0", name="ck_entitlements_duration_days_positivo"),
        CheckConstraint("expires_at > starts_at", name="ck_entitlements_expires_at_maior_que_starts_at"),
        CheckConstraint(sql_in("status", ENTITLEMENT_STATUSES), name="ck_entitlements_status_valido"),
        CheckConstraint(
            "(status = 'granted' AND revoked_at IS NULL) OR (status = 'revoked' AND revoked_at IS NOT NULL)",
            name="ck_entitlements_revogacao_consistente",
        ),
        Index("ix_entitlements_user_id_status_expires_at", "user_id", "status", "expires_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", name="fk_entitlements_user_id_users", ondelete="RESTRICT"),
        nullable=False,
    )
    plan_code: Mapped[str] = mapped_column(String(32), nullable=False)
    payment_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("payments.id", name="fk_entitlements_payment_id_payments", ondelete="RESTRICT"),
        nullable=False,
    )
    duration_days: Mapped[int] = mapped_column(Integer, nullable=False)
    starts_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="granted")
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    revoke_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utcnow, onupdate=utcnow
    )
