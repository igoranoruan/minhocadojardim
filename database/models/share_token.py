from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base
from database.types import UTCDateTime, utcnow


class ShareToken(Base):
    """Token pessoal do atalho "Enviar para o KLANGO" (compartilhar direto do Instagram/TikTok/
    Pinterest/YouTube para a fila, sem login/cookie -- ver services/share_queue.py). O banco guarda
    só o SHA-256 do token (mesmo padrão de AuthSession) -- o valor puro só existe no momento em que
    é gerado, devolvido uma única vez na resposta de POST /api/share/token.

    Escopo DELIBERADAMENTE limitado: este token só autentica POST /api/ext/share (adicionar um link
    à fila). Não serve para nenhuma outra rota -- não baixa, não lê dados do usuário, não acessa
    pagamento. Quem verifica isso é routes/share_ext.py (nunca usa get_current_user/cookie) e
    services/share_queue.py::authenticate_share_token (a ÚNICA função que aceita este token).

    Invariável "no máximo 1 token ATIVO por usuário" (gerar novo revoga o automaticamente o
    anterior -- aprovação do CÉREBRO): garantida pelo banco com UNIQUE(user_id, active_slot), MESMO
    idioma já usado em LoginCode (ver database/models/login_code.py) -- active_slot = 1 enquanto
    ativo, NULL quando revogado; como NULL nunca colide com NULL, pode haver vários revogados, mas
    só um ativo. `revoked_at` carrega o significado (NULL = ativo); `active_slot` é só o mecanismo
    do CHECK/UNIQUE que torna isso uma garantia do BANCO, não só da ordem de execução do serviço.
    """

    __tablename__ = "share_tokens"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_share_tokens_token_hash"),
        UniqueConstraint("user_id", "active_slot", name="uq_share_tokens_user_id_active_slot"),
        CheckConstraint("length(token_hash) = 64", name="ck_share_tokens_token_hash_64"),
        CheckConstraint("active_slot IS NULL OR active_slot = 1", name="ck_share_tokens_active_slot_valido"),
        # Mesma lógica de LoginCode.ck_login_codes_aberto_ou_fechado: ativo = active_slot=1 E
        # revoked_at NULL; revogado = active_slot NULL E revoked_at preenchido -- nunca um meio-termo.
        CheckConstraint(
            "(active_slot IS NOT NULL AND active_slot = 1 AND revoked_at IS NULL) "
            "OR (active_slot IS NULL AND revoked_at IS NOT NULL)",
            name="ck_share_tokens_ativo_ou_revogado",
        ),
        Index("ix_share_tokens_user_id", "user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", name="fk_share_tokens_user_id_users", ondelete="RESTRICT"),
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    active_slot: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
