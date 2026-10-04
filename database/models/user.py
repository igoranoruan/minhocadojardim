from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, UniqueConstraint
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
        UniqueConstraint("referral_code", name="uq_users_referral_code"),
        CheckConstraint(
            "referred_by_user_id IS NULL OR referred_by_user_id != id",
            name="ck_users_nao_auto_indicacao",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    email_verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # Etapa 8 (programa de indicação, aprovação do CÉREBRO, 03/10/2026): código curto e único para
    # compartilhar (klango.site/?ref=CODIGO). NULL até a primeira vez que o próprio usuário pedir
    # seu link (GET /api/me/referral) -- gerado SOB DEMANDA por services/referrals.py, nunca em
    # massa para contas já existentes (inclusive toda identidade Free anônima -- ver
    # database/models/anonymous_identity.py --, que nunca chega a pedir esse endpoint, já que ele
    # exige login de verdade).
    referral_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Quem indicou este usuário -- gravado UMA ÚNICA VEZ (nunca sobrescrito depois de setado), no
    # momento em que o PRÓPRIO usuário reivindica o código de outra pessoa logo após o login
    # (services/referrals.py::claim_referral). NULL para todo usuário que nunca reivindicou um
    # código (inclusive todos os já existentes antes desta coluna, e toda identidade Free anônima).
    referred_by_user_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("users.id", name="fk_users_referred_by_user_id_users", ondelete="RESTRICT"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utcnow, onupdate=utcnow
    )

    @validates("email")
    def _normalizar_email(self, _key: str, value):
        return normalize_email(value) if isinstance(value, str) else value
