from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base
from database.types import UTCDateTime, utcnow


class AudioBatch(Base):
    """Lote de EXTRAÇÃO DE ÁUDIO (Parte B, autorização do CÉREBRO, 11/10/2026).

    Tabela DELIBERADAMENTE separada de `batches` (database/models/batch.py): um lote de áudio NUNCA
    pode contar na cota semanal de OPERAÇÕES de lote dos planos pagos (services/usage.py::
    _count_batches_in_week só enxerga a tabela `batches`) -- combinar as duas coisas na mesma
    tabela exigiria uma coluna/flag para diferenciar "é vídeo ou é áudio" e arriscaria contaminar
    aquela contagem por engano, além de obrigar a alterar a migration que criou `batches` (proibido
    pela autorização: nunca alterar migration já existente). Duas tabelas iguais na FORMA, mas
    totalmente isoladas na regra de negócio, é a opção mais segura.

    Mesmo desenho de database/models/batch.py (ver aquele arquivo para o raciocínio completo de
    cada campo) -- só o nome da tabela/constraints muda.
    """

    __tablename__ = "audio_batches"
    __table_args__ = (
        UniqueConstraint("user_id", "request_id", name="uq_audio_batches_user_id_request_id"),
        CheckConstraint("item_count >= 1", name="ck_audio_batches_item_count_minimo"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", name="fk_audio_batches_user_id_users", ondelete="RESTRICT"),
        nullable=False,
    )
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    item_count: Mapped[int] = mapped_column(Integer, nullable=False)
    # Fingerprint determinístico do conteúdo do lote (mesmo papel de Batch.request_fingerprint) --
    # usado por services/audio_usage.py::_audio_batch_replay para distinguir "mesmo request_id,
    # mesmo conteúdo" (replay idempotente) de "mesmo request_id, conteúdo diferente" (conflito).
    request_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
