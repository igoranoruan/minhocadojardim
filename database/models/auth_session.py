from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base
from database.types import UTCDateTime, utcnow


class AuthSession(Base):
    """Sessão autenticada (server-side). O banco guarda só o SHA-256 do token.

    A sessão expira em `expires_at` (30 dias fixos a partir da criação) e NÃO é renovada
    a cada requisição. `revoked_at` preenchido = sessão encerrada (logout).
    """

    __tablename__ = "auth_sessions"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_auth_sessions_token_hash"),
        CheckConstraint("length(token_hash) = 64", name="ck_auth_sessions_token_hash_64"),
        CheckConstraint("expires_at > created_at", name="ck_auth_sessions_expires_at_maior_que_created_at"),
        Index("ix_auth_sessions_user_id", "user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", name="fk_auth_sessions_user_id_users", ondelete="RESTRICT"),
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    last_used_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
