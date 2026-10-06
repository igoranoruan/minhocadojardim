"""Comentários públicos do site (05/10/2026, aprovação do CÉREBRO).

Regras:
- Leitura: pública, qualquer um (routes/comments.py::list_comments_route, sem autenticação).
- Escrita: só quem tem sessão autenticada E plano pago VIGENTE agora
  (services.entitlements.get_current_entitlement não-None) -- não "já teve algum dia", é o estado
  no INSTANTE do post. Depois que o plano expira, o comentário já publicado continua (nunca é
  removido por expiração).
- Exclusão: só o admin (routes/deps.py::require_admin) -- hard delete, sem recuperação.

Os 5 comentários de exemplo (is_example=True, user_id=None) foram inseridos pela migration 0009,
nunca por este módulo -- toda escrita daqui cria um comentário REAL (is_example sempre False).
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import Comment
from services.entitlements import get_current_entitlement

MAX_AUTHOR_NAME_LENGTH = 80
MAX_BODY_LENGTH = 500


class CommentError(Exception):
    """Erro de regra de comentário."""


class InvalidCommentError(CommentError):
    """Nome ou corpo do comentário fora do tamanho permitido."""


class NoActivePlanError(CommentError):
    """Usuário sem plano pago vigente agora -- não pode comentar."""


class CommentNotFoundError(CommentError):
    pass


def list_comments(db: Session) -> list[Comment]:
    """Todos os comentários, mais recentes primeiro. Pública -- sem filtro de usuário."""
    return list(db.execute(select(Comment).order_by(Comment.created_at.desc())).scalars().all())


def create_comment(db: Session, *, user_id: int, author_name: str, body: str) -> Comment:
    """Cria um comentário REAL. Levanta NoActivePlanError se o usuário não tiver plano pago
    vigente agora -- a checagem é SEMPRE no instante do post, nunca "já teve algum dia"."""
    author_name = author_name.strip()
    body = body.strip()
    if not author_name or len(author_name) > MAX_AUTHOR_NAME_LENGTH:
        raise InvalidCommentError(f"Nome deve ter de 1 a {MAX_AUTHOR_NAME_LENGTH} caracteres.")
    if not body or len(body) > MAX_BODY_LENGTH:
        raise InvalidCommentError(f"Comentário deve ter de 1 a {MAX_BODY_LENGTH} caracteres.")
    if get_current_entitlement(db, user_id) is None:
        raise NoActivePlanError()
    comment = Comment(user_id=user_id, author_name=author_name, body=body, is_example=False)
    db.add(comment)
    db.commit()
    db.refresh(comment)
    return comment


def delete_comment(db: Session, comment_id: int) -> None:
    """Remoção definitiva (hard delete) -- só chamada pela rota admin-only."""
    comment = db.execute(select(Comment).where(Comment.id == comment_id)).scalar_one_or_none()
    if comment is None:
        raise CommentNotFoundError(f"Comentário {comment_id} não encontrado.")
    db.delete(comment)
    db.commit()
