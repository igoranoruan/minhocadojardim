from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base
from database.types import UTCDateTime, utcnow


class ShareQueueItem(Base):
    """Item da fila do atalho "Enviar para o KLANGO" (ver database/models/share_token.py e
    services/share_queue.py). Cada linha é UM link que o usuário compartilhou do app da rede
    social, já validado e normalizado (sem parâmetros de rastreio), esperando para ser enviado a
    um lote.

    `status`: 'pending' (na fila, ainda não usado) -> 'consumed' (incluído num envio para o lote
    via POST /api/share/queue/send-to-batch -- o DOWNLOAD em si continua acontecendo só quando o
    usuário efetivamente confirma o lote normal em /api/download-batch; esta tabela nunca consome
    cota/gera Generation sozinha) ou 'removed' (removido manualmente pelo usuário, ou fila limpa).
    Nenhuma linha é apagada de verdade -- só muda de status (histórico simples, sem TTL/limpeza
    nesta etapa).

    Deduplicação (mesma URL pendente duas vezes para o mesmo usuário) é feita na APLICAÇÃO
    (services/share_queue.py), não por uma constraint do banco: um índice único parcial
    (user_id, url) só entre pendentes teria sintaxe diferente em SQLite e PostgreSQL, e o risco
    real de duas requisições concorrentes exatamente da MESMA URL no MESMO instante é baixo (pior
    caso: 1 item duplicado na fila, removível manualmente) -- ver limitação documentada no relatório
    da Etapa 2.
    """

    __tablename__ = "share_queue_items"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'consumed', 'removed')", name="ck_share_queue_items_status_valido"
        ),
        Index("ix_share_queue_items_user_id_status", "user_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", name="fk_share_queue_items_user_id_users", ondelete="RESTRICT"),
        nullable=False,
    )
    # URL já NORMALIZADA (sem parâmetros de rastreio -- services/share_queue.py::normalize_share_url)
    # -- nunca a URL crua como veio do compartilhamento.
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    consumed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
