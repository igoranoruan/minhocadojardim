from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base, sql_in
from database.types import UTCDateTime, utcnow

GENERATION_STATUSES = ("reserved", "completed", "failed")


class Generation(Base):
    """Livro permanente de consumo (uma linha por geração).

    Estados: reserved -> completed | failed (as transições são do serviço, etapa 4).
    period_day / period_week são preenchidos pelo serviço de uso (regra de São Paulo).
    output_sha256 é o SHA-256 do arquivo FINAL de saída, nulo até a conclusão.

    output_size_bytes / output_expires_at / output_storage_key (Etapa 8B.1 — só schema: nada
    ainda escreve nestes campos) descrevem o RESULTADO disponível para download, quando existir.
    output_storage_key é uma referência ABSTRATA (não um caminho de filesystem): o "onde" e o
    "como" resolver essa chave para o arquivo real é decisão da camada de armazenamento, ainda não
    implementada. Todos os três são NULL para qualquer geração sem resultado entregável — inclusive
    todas as gerações já existentes antes desta etapa, que continuam válidas exatamente como estão.
    """

    __tablename__ = "generations"
    __table_args__ = (
        UniqueConstraint("user_id", "request_id", name="uq_generations_user_id_request_id"),
        UniqueConstraint("batch_id", "position", name="uq_generations_batch_id_position"),
        CheckConstraint(sql_in("status", GENERATION_STATUSES), name="ck_generations_status_valido"),
        # Avulsa: sem lote e com request_id. De lote: com batch_id e position, sem request_id.
        CheckConstraint(
            "(batch_id IS NULL AND position IS NULL AND request_id IS NOT NULL) "
            "OR (batch_id IS NOT NULL AND position IS NOT NULL AND request_id IS NULL)",
            name="ck_generations_avulsa_ou_lote",
        ),
        CheckConstraint("position IS NULL OR position >= 1", name="ck_generations_position_minimo"),
        CheckConstraint("duration_ms IS NULL OR duration_ms >= 0", name="ck_generations_duration_ms_nao_negativo"),
        CheckConstraint(
            "output_sha256 IS NULL OR length(output_sha256) = 64",
            name="ck_generations_output_sha256_64",
        ),
        CheckConstraint(
            "output_size_bytes IS NULL OR output_size_bytes >= 0",
            name="ck_generations_output_size_bytes_nao_negativo",
        ),
        Index(
            "ix_generations_user_id_period_week_plan_code_status",
            "user_id", "period_week", "plan_code", "status",
        ),
        Index(
            "ix_generations_user_id_period_day_plan_code_status",
            "user_id", "period_day", "plan_code", "status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", name="fk_generations_user_id_users", ondelete="RESTRICT"),
        nullable=False,
    )
    batch_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("batches.id", name="fk_generations_batch_id_batches", ondelete="RESTRICT"),
        nullable=True,
    )
    position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    plan_code: Mapped[str] = mapped_column(String(32), nullable=False)
    entitlement_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("entitlements.id", name="fk_generations_entitlement_id_entitlements", ondelete="RESTRICT"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="reserved")
    period_day: Mapped[date] = mapped_column(Date, nullable=False)
    period_week: Mapped[date] = mapped_column(Date, nullable=False)
    platform: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    output_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    output_storage_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
