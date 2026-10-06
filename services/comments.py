"""Comentários públicos do site (05/10/2026, aprovação do CÉREBRO).

Regras:
- Leitura pública (list_comments): só comentários approved=True E is_example=False -- qualquer um
  vê, sem autenticação (routes/comments.py::get_comments).
- Escrita (create_comment): só quem tem sessão autenticada E plano pago VIGENTE agora
  (services.entitlements.get_current_entitlement não-None) -- não "já teve algum dia", é o estado
  no INSTANTE do post. Nasce approved=False (fila de moderação) -- nunca aparece publicamente
  antes de o admin aprovar. Depois que o plano expira, o comentário já aprovado continua (nunca é
  removido por expiração).
- Aprovação (approve_comment) e exclusão/rejeição (delete_comment): só o admin
  (routes/deps.py::require_admin). Não existe um terceiro estado "rejeitado" -- rejeitar É apagar.

Os 5 comentários de exemplo (is_example=True, user_id=None) foram inseridos pela migration 0009,
nunca por este módulo -- toda escrita daqui cria um comentário REAL (is_example sempre False).

06/10/2026 (pedido do CÉREBRO, 1ª parte): list_comments() não devolve mais os is_example=True para
o público -- o selo "Exemplo" no nome estava, na avaliação do CÉREBRO, prejudicando mais a
credibilidade da seção do que ajudando a quebrar objeção (intenção original da Etapa). Removê-lo e
continuar exibindo os 5 textos fictícios como se fossem reais não é uma opção (depoimento
inventado apresentado como genuíno, o que o próprio projeto já tratou como vedado -- ver
services/stats.py). A alternativa escolhida, dentre as que o próprio CÉREBRO apontou como
aceitáveis, foi recolher os exemplos de circulação: a seção passa a mostrar só comentários reais,
com o estado vazio ("Seja o primeiro a comentar.") cobrindo o período até o primeiro. As 5 linhas
continuam no banco (nunca apagadas, migrations 0009/0010 intactas).

06/10/2026 (pedido do CÉREBRO, 2ª parte): o formulário foi liberado e qualquer plano pago vigente
publicava direto, sem revisão -- risco real (ninguém impede um comentário ofensivo/spam/com dado
pessoal de terceiro de ir ao ar na hora). migration 0011 adiciona comments.approved; a partir daqui
todo comentário real nasce pendente e só o CÉREBRO decide o que entra no site (ver
routes/comments.py e static/admin.html).
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
    """Comentários REAIS e APROVADOS, mais recentes primeiro. Pública -- sem filtro de usuário,
    mas nunca devolve is_example=True (seed ilustrativo, ver docstring do módulo) nem
    approved=False (ainda na fila de moderação)."""
    return list(
        db.execute(
            select(Comment)
            .where(Comment.is_example.is_(False))
            .where(Comment.approved.is_(True))
            .order_by(Comment.created_at.desc())
        ).scalars().all()
    )


def list_pending_comments(db: Session) -> list[Comment]:
    """Fila de moderação: comentários REAIS ainda não aprovados, mais antigos primeiro (ordem de
    chegada) -- só para o admin (routes/comments.py::get_pending_comments, require_admin)."""
    return list(
        db.execute(
            select(Comment)
            .where(Comment.is_example.is_(False))
            .where(Comment.approved.is_(False))
            .order_by(Comment.created_at.asc())
        ).scalars().all()
    )


def create_comment(db: Session, *, user_id: int, author_name: str, body: str) -> Comment:
    """Cria um comentário REAL, sempre approved=False (fila de moderação). Levanta
    NoActivePlanError se o usuário não tiver plano pago vigente agora -- a checagem é SEMPRE no
    instante do post, nunca "já teve algum dia"."""
    author_name = author_name.strip()
    body = body.strip()
    if not author_name or len(author_name) > MAX_AUTHOR_NAME_LENGTH:
        raise InvalidCommentError(f"Nome deve ter de 1 a {MAX_AUTHOR_NAME_LENGTH} caracteres.")
    if not body or len(body) > MAX_BODY_LENGTH:
        raise InvalidCommentError(f"Comentário deve ter de 1 a {MAX_BODY_LENGTH} caracteres.")
    if get_current_entitlement(db, user_id) is None:
        raise NoActivePlanError()
    comment = Comment(user_id=user_id, author_name=author_name, body=body, is_example=False, approved=False)
    db.add(comment)
    db.commit()
    db.refresh(comment)
    return comment


def approve_comment(db: Session, comment_id: int) -> Comment:
    """Publica um comentário pendente -- só chamada pela rota admin-only."""
    comment = db.execute(select(Comment).where(Comment.id == comment_id)).scalar_one_or_none()
    if comment is None:
        raise CommentNotFoundError(f"Comentário {comment_id} não encontrado.")
    comment.approved = True
    db.commit()
    db.refresh(comment)
    return comment


def delete_comment(db: Session, comment_id: int) -> None:
    """Remoção definitiva (hard delete) -- também é como um comentário pendente é REJEITADO (não
    existe um terceiro estado além de pendente/aprovado). Só chamada pela rota admin-only."""
    comment = db.execute(select(Comment).where(Comment.id == comment_id)).scalar_one_or_none()
    if comment is None:
        raise CommentNotFoundError(f"Comentário {comment_id} não encontrado.")
    db.delete(comment)
    db.commit()
