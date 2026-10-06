from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base
from database.types import UTCDateTime, utcnow


class Comment(Base):
    """Comentários públicos do site (05/10/2026, aprovação do CÉREBRO): qualquer visitante LÊ, sem
    login -- o objetivo é quebrar objeção de quem ainda não assinou, então esconder de quem não
    pagou seria contraproducente.

    Só COMENTA quem tem sessão autenticada E plano pago VIGENTE no instante do post
    (services/comments.py::create_comment, via services.entitlements.get_current_entitlement --
    não "já teve algum dia", é o estado agora). Se o plano expirar depois, o comentário já
    publicado continua valendo -- nunca é removido por expiração, só por remoção manual do
    CÉREBRO (routes/comments.py::delete_comment, admin-only).

    user_id é NULO só para os 5 comentários DE EXEMPLO (is_example=True) que a migration 0009 já
    insere, para a seção não nascer vazia -- nunca apresentados como depoimento real (o frontend
    rotula "Exemplo" sempre que is_example é True, nunca um comentário comum sem essa marcação).
    Para um comentário real, user_id aponta pra quem postou e author_name é o nome que a própria
    pessoa digitou no momento (não existe campo de nome em User hoje -- ver
    database/models/user.py).

    Exclusão é SEMPRE definitiva (hard delete, nunca soft-delete/flag): services/comments.py é o
    único módulo que escreve nesta tabela.

    approved (06/10/2026, migration 0011, aprovação do CÉREBRO): moderação prévia -- todo
    comentário REAL nasce approved=False (fila de espera) e só aparece publicamente depois que o
    CÉREBRO aprova manualmente (routes/comments.py::approve_comment_route, admin-only, mesmo
    require_admin do delete). Antes disso a leitura pública era imediata (qualquer um postava e já
    aparecia no site); isso deixou de ser aceitável assim que o formulário foi liberado de verdade.
    Rejeitar um comentário pendente é a própria exclusão (DELETE já existente) -- não existe um
    terceiro estado "rejeitado", só "pendente" (approved=False) ou "publicado" (approved=True).
    """

    __tablename__ = "comments"
    __table_args__ = (
        CheckConstraint("length(trim(author_name)) >= 1", name="ck_comments_author_name_nao_vazio"),
        CheckConstraint("length(trim(body)) >= 1", name="ck_comments_body_nao_vazio"),
        Index("ix_comments_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("users.id", name="fk_comments_user_id_users", ondelete="RESTRICT"),
        nullable=True,
    )
    author_name: Mapped[str] = mapped_column(String(80), nullable=False)
    body: Mapped[str] = mapped_column(String(500), nullable=False)
    is_example: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    approved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
