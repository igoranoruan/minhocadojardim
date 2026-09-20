from datetime import datetime

from sqlalchemy import CheckConstraint, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, validates

from database.base import Base
from database.types import UTCDateTime, utcnow


def normalize_email(value: str) -> str:
    """Única normalização de e-mail do projeto: strip + lower (sem regras de Gmail)."""
    return value.strip().lower()


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("email", name="uq_users_email"),
        CheckConstraint("email = lower(trim(email))", name="ck_users_email_normalizado"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    email_verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utcnow, onupdate=utcnow
    )

    @validates("email")
    def _normalizar_email(self, _key: str, value):
        return normalize_email(value) if isinstance(value, str) else value
