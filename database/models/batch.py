from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base
from database.types import UTCDateTime, utcnow


class Batch(Base):
    """Lote de vídeos. Sem coluna de status: o estado do lote é derivado das generations.

    O teto de vídeos por lote (max_batch_size) varia por plano e NÃO é exclusivo do VIP —
    hoje Semanal, Mensal e VIP Batch podem usar lote, cada um com seu próprio teto; só o Free
    não usa lote. Esses valores são regra de serviço (services/plans.py, fonte única do
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
    # Fingerprint determinístico (SHA-256) do conteúdo do lote -- URLs, filenames e ORDEM (Etapa
    # 9.3, correção de idempotência real). NULLABLE só por compatibilidade com lotes criados antes
    # desta coluna existir (ver a migration 0005); todo lote novo sempre grava um valor. Usado
    # apenas para detectar "mesmo request_id, conteúdo diferente" em reserve_batch/_batch_replay
    # -- nunca em nenhum filtro/consulta (sem índice).
    request_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)