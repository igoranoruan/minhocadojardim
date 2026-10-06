"""Comentários públicos do site (05/10/2026, aprovação do CÉREBRO).

GET    /api/comments       lista todos, mais recentes primeiro -- PÚBLICA, sem autenticação (o
                            objetivo é quebrar objeção de quem ainda não assinou).
POST   /api/comments       cria um comentário -- exige sessão autenticada E plano pago VIGENTE
                            agora (services/comments.py::create_comment). Expirar depois NÃO
                            apaga o comentário já publicado.
DELETE /api/comments/{id}  remove definitivamente -- só o admin (routes/deps.py::require_admin).

Rota fina, mesmo padrão das demais: toda regra de negócio mora em services/comments.py; aqui só
se resolve a identidade (sessão) e traduz exceção para HTTP ({"detail": "...", "code": "..."}).
"""
import re
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database.models import User
from database.session import get_session
from routes.deps import get_current_user, require_admin
from services.comments import (
    CommentError,
    CommentNotFoundError,
    InvalidCommentError,
    NoActivePlanError,
    create_comment,
    delete_comment,
    list_comments,
)

router = APIRouter(prefix="/api/comments", tags=["comments"])

NO_STORE = {"Cache-Control": "no-store"}

_CAMEL_CASE_RE = re.compile(r"(?<!^)(?=[A-Z])")


def _code_for(exc: BaseException) -> str:
    name = exc.__class__.__name__
    if name.endswith("Error"):
        name = name[: -len("Error")]
    return _CAMEL_CASE_RE.sub("_", name).lower()


def _error_response(status_code: int, exc: BaseException, *, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"detail": detail, "code": _code_for(exc)}, headers=NO_STORE)


async def comment_error_handler(request: Request, exc: CommentError) -> JSONResponse:
    """Erro de validação (nome/corpo fora do tamanho) ou sem plano pago vigente -- 422 para ambos,
    mesma filosofia de routes/generations.py::download_error_handler (erro do pedido, não nosso)."""
    if isinstance(exc, NoActivePlanError):
        detail = "Só quem tem um plano pago vigente pode comentar."
    elif isinstance(exc, InvalidCommentError):
        detail = str(exc)
    else:
        detail = "Não foi possível publicar o comentário."
    return _error_response(422, exc, detail=detail)


async def comment_not_found_handler(request: Request, exc: CommentNotFoundError) -> JSONResponse:
    return _error_response(404, exc, detail="Comentário não encontrado.")


class CommentOut(BaseModel):
    id: int
    author_name: str
    body: str
    is_example: bool
    created_at: datetime


class CreateCommentBody(BaseModel):
    author_name: str = Field(min_length=1, max_length=80)
    body: str = Field(min_length=1, max_length=500)


def _out(comment) -> CommentOut:
    return CommentOut(
        id=comment.id,
        author_name=comment.author_name,
        body=comment.body,
        is_example=comment.is_example,
        created_at=comment.created_at,
    )


@router.get("", response_model=list[CommentOut])
def get_comments(db: Session = Depends(get_session)) -> list[CommentOut]:
    return [_out(c) for c in list_comments(db)]


@router.post("", response_model=CommentOut)
def post_comment(
    body: CreateCommentBody,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> CommentOut:
    comment = create_comment(db, user_id=user.id, author_name=body.author_name, body=body.body)
    return _out(comment)


@router.delete("/{comment_id}")
def remove_comment(
    comment_id: int,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> JSONResponse:
    delete_comment(db, comment_id)
    return JSONResponse({"status": "deleted"}, headers=NO_STORE)
