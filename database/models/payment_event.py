from datetime import datetime

from sqlalchemy import Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base
from database.types import UTCDateTime, utcnow


class PaymentEvent(Base):
    """Log de auditoria das notificações do provedor de pagamento.

    Não bloqueia duplicidade de propósito: a idempotência está nos pagamentos
    (external_reference / mp_payment_id únicos) e no acesso (payment_id único).
    O payload guarda JSON serializado (texto), mínimo necessário.
    """

    __tablename__ = "payment_events"
    __table_args__ = (
        Index("ix_payment_events_provider_resource_id", "provider", "resource_id"),
        Index("ix_payment_events_received_at", "received_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    event_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    action: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    result: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
