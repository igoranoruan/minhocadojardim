from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base, sql_in
from database.types import UTCDateTime, utcnow

AUDIO_EXTRACTION_STATUSES = ("reserved", "completed", "failed")


class AudioExtraction(Base):
    """Ledger de EXTRAÇÃO DE ÁUDIO (Parte A/B, autorização do CÉREBRO, 11/10/2026) -- mesmo
    DESENHO de database/models/generation.py (reserved -> completed | failed; avulsa OU de lote,
    nunca os dois ao mesmo tempo), mas uma tabela TOTALMENTE separada de `generations`: a extração
    de áudio NUNCA consome geração (pedido explícito do CÉREBRO). Se reaproveitássemos
    `generations` para isto, qualquer contagem de cota de vídeo (services/usage.py::
    _compute_allowance) teria que aprender a ignorar linhas de áudio, arriscando um bug sutil numa
    tabela crítica e já testada -- uma tabela própria elimina esse risco por completo.

    period_day é preenchido por services/audio_usage.py, usando a MESMA regra de dia de São Paulo
    (utils/time_sp.py) já usada pelos planos pagos -- só dia, nunca semana (o limite de áudio é
    SEMPRE diário, somando avulsa + lote, nunca dois contadores separados).

    batch_id/position só são preenchidos a partir da Parte B (lote) -- uma extração avulsa (Parte
    A) sempre tem request_id e nunca tem batch_id/position (mesmo CHECK de
    database/models/generation.py::ck_generations_avulsa_ou_lote, replicado abaixo).
    """

    __tablename__ = "audio_extractions"
    __table_args__ = (
        UniqueConstraint("user_id", "request_id", name="uq_audio_extractions_user_id_request_id"),
        UniqueConstraint("batch_id", "position", name="uq_audio_extractions_batch_id_position"),
        CheckConstraint(sql_in("status", AUDIO_EXTRACTION_STATUSES), name="ck_audio_extractions_status_valido"),
        # Avulsa: sem lote e com request_id. De lote: com batch_id e position, sem request_id.
        CheckConstraint(
            "(batch_id IS NULL AND position IS NULL AND request_id IS NOT NULL) "
            "OR (batch_id IS NOT NULL AND position IS NOT NULL AND request_id IS NULL)",
            name="ck_audio_extractions_avulsa_ou_lote",
        ),
        CheckConstraint("position IS NULL OR position >= 1", name="ck_audio_extractions_position_minimo"),
        CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0", name="ck_audio_extractions_duration_ms_nao_negativo"
        ),
        CheckConstraint(
            "output_sha256 IS NULL OR length(output_sha256) = 64",
            name="ck_audio_extractions_output_sha256_64",
        ),
        CheckConstraint(
            "output_size_bytes IS NULL OR output_size_bytes >= 0",
            name="ck_audio_extractions_output_size_bytes_nao_negativo",
        ),
        Index("ix_audio_extractions_user_id_period_day_status", "user_id", "period_day", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", name="fk_audio_extractions_user_id_users", ondelete="RESTRICT"),
        nullable=False,
    )
    batch_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("audio_batches.id", name="fk_audio_extractions_batch_id_audio_batches", ondelete="RESTRICT"),
        nullable=True,
    )
    position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="reserved")
    period_day: Mapped[date] = mapped_column(Date, nullable=False)
    platform: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    output_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    output_storage_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Nome de exibição opcional (mesmo papel de Generation.display_filename) -- já SANITIZADO por
    # quem chama (services.batch_filenames.sanitize_batch_filename, com suffix=".mp3") antes de
    # chegar aqui; NULL vira o padrão "audio_klango.mp3" na hora do download (routes/audio.py).
    display_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
