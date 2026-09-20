from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base
from database.types import UTCDateTime, utcnow


class Batch(Base):
    """Lote de vídeos. Sem coluna de status: o estado do lote é derivado das generations.

    O máximo de 10 itens e a exclusividade do VIP são regras do serviço (config.py e
    catálogo de planos), não do banco.
    """

    __tablename__ = "batches"
    __table_args__ = (
        UniqueConstraint("user_id", "request_id", name="uq_batches_user_id_request_id"),
        CheckConstraint("item_count >= 1", name="ck_batches_item_count_minimo"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", name="fk_batches_user_id_users", ondelete="RESTRICT"),
        nullable=False,
    )
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    item_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
