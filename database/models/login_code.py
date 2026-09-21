from datetime import datetime

from sqlalchemy import CheckConstraint, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base
from database.types import UTCDateTime, utcnow

CLOSE_REASONS = ("verified", "superseded", "exhausted", "send_failed")


class LoginCode(Base):
    """Código de login enviado por e-mail (um uso). Guarda só o HMAC do código.

    Invariável: no máximo UM código aberto por e-mail. É garantida pelo banco com
    UNIQUE(email, active_slot): enquanto o código está aberto, active_slot = 1; ao fechar
    (verificado, substituído, esgotado ou falha de envio) active_slot vira NULL. Como NULL
    nunca colide com NULL, pode haver vários fechados, mas só um aberto. Funciona igual no
    SQLite e no PostgreSQL, sem índice parcial.

    Sem FK para users: o usuário só é criado depois que o código correto é informado.
    """

    __tablename__ = "login_codes"
    __table_args__ = (
        UniqueConstraint("email", "active_slot", name="uq_login_codes_email_active_slot"),
        CheckConstraint("email = lower(trim(email))", name="ck_login_codes_email_normalizado"),
        CheckConstraint("length(code_hash) = 64", name="ck_login_codes_code_hash_64"),
        CheckConstraint("attempts >= 0", name="ck_login_codes_attempts_nao_negativo"),
        CheckConstraint("expires_at > created_at", name="ck_login_codes_expires_at_maior_que_created_at"),
        CheckConstraint("active_slot IS NULL OR active_slot = 1", name="ck_login_codes_active_slot_valido"),
        CheckConstraint(
            "close_reason IS NULL OR close_reason IN ('verified', 'superseded', 'exhausted', 'send_failed')",
            name="ck_login_codes_close_reason_valido",
        ),
        # Aberto: active_slot = 1 e sem fechamento. Fechado: active_slot NULL e com closed_at e close_reason.
        # O "IS NOT NULL" antes de "= 1" evita o resultado NULL do SQL (que um CHECK aceita) no caso
        # em que active_slot, closed_at e close_reason são todos NULL.
        CheckConstraint(
            "(active_slot IS NOT NULL AND active_slot = 1 AND closed_at IS NULL AND close_reason IS NULL) "
            "OR (active_slot IS NULL AND closed_at IS NOT NULL AND close_reason IS NOT NULL)",
            name="ck_login_codes_aberto_ou_fechado",
        ),
        Index("ix_login_codes_email_created_at", "email", "created_at"),
        Index("ix_login_codes_ip_hash_created_at", "ip_hash", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    close_reason: Mapped[str | None] = mapped_column(String(16), nullable=True)
    active_slot: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
