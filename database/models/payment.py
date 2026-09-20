from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, validates

from database.base import Base, sql_in
from database.types import UTCDateTime, utcnow

PAYMENT_METHODS = ("pix", "credit_card")
PAYMENT_STATUSES = ("pending", "approved", "rejected", "cancelled", "refunded", "charged_back")


class Payment(Base):
    """Compra única (sem recorrência). O serviço de pagamento vem em etapa posterior."""

    __tablename__ = "payments"
    __table_args__ = (
        UniqueConstraint("mp_payment_id", name="uq_payments_mp_payment_id"),
        UniqueConstraint("external_reference", name="uq_payments_external_reference"),
        CheckConstraint("amount_cents > 0", name="ck_payments_amount_cents_positivo"),
        CheckConstraint(sql_in("method", PAYMENT_METHODS), name="ck_payments_method_valido"),
        CheckConstraint(sql_in("status", PAYMENT_STATUSES), name="ck_payments_status_valido"),
        Index("ix_payments_user_id_created_at", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", name="fk_payments_user_id_users", ondelete="RESTRICT"),
        nullable=False,
    )
    plan_code: Mapped[str] = mapped_column(String(32), nullable=False)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    mp_payment_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    external_reference: Mapped[str] = mapped_column(String(64), nullable=False)
    status_detail: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    refunded_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utcnow, onupdate=utcnow
    )

    @validates("amount_cents")
    def _validar_amount_cents(self, _key: str, value):
        # Dinheiro é sempre inteiro em centavos: nada de float (nem bool).
        if value is None:
            return value
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("amount_cents deve ser um inteiro em centavos (ex.: 990), nunca float.")
        return value
