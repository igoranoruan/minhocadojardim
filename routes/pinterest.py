"""Listagem de pasta/coleção do Pinterest (Etapa 3, 10/10/2026 -- aprovação do CÉREBRO).

POST /api/pinterest/board-list  lista os pins de uma pasta pública do Pinterest -- só leitura de
                                 metadados, NUNCA baixa vídeo nenhum. Exige sessão autenticada COM
                                 plano que tenha lote habilitado (Semanal/Mensal/VIP Batch; Free
                                 recebe 403) e está sujeita a rate limit por usuário (ver
                                 services/pinterest_board.py).

Rota fina, mesmo padrão das demais: toda regra de negócio mora em services/pinterest_board.py e
download/board_listing.py; aqui só se resolve a identidade (sessão) e traduz exceção para HTTP.

Resultado desta listagem NÃO cria nenhum registro em `generations`/`batches` nem consome cota --
baixar os pins escolhidos continua sendo feito pelo /api/download-batch já existente (etapa futura
do frontend monta essa chamada a partir dos pins selecionados aqui)."""
import re

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database.models import User
from database.session import get_session
from routes.deps import get_current_user
from services.pinterest_board import PinterestBoardError, list_board

router = APIRouter(prefix="/api/pinterest", tags=["pinterest"])

NO_STORE = {"Cache-Control": "no-store"}

_CAMEL_CASE_RE = re.compile(r"(?<!^)(?=[A-Z])")


def _code_for(exc: BaseException) -> str:
    name = exc.__class__.__name__
    if name.endswith("Error"):
        name = name[: -len("Error")]
    return _CAMEL_CASE_RE.sub("_", name).lower()


async def pinterest_board_error_handler(request: Request, exc: PinterestBoardError) -> JSONResponse:
    """Um único handler para todos os erros desta rota -- cada subclasse já carrega seu próprio
    status_code/user_message (ver services/pinterest_board.py), mesma filosofia de
    routes/generations.py::download_error_handler (mensagem pronta e segura em exc.detail)."""
    headers = dict(NO_STORE)
    retry_after = getattr(exc, "retry_after_seconds", None)
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "code": _code_for(exc)},
        headers=headers,
    )


class BoardListBody(BaseModel):
    url: str = Field(min_length=1, max_length=2048)


class BoardPinOut(BaseModel):
    pin_id: str
    pin_url: str
    thumbnail_url: str | None
    has_video: bool
    title: str | None  # 10/10/2026: texto livre do Pinterest, NUNCA sanitizado aqui -- só
    # sugestão de nome de arquivo para o frontend; a sanitização real acontece em
    # services/batch_filenames.py quando o nome de fato vira filename de uma geração.


class BoardListOut(BaseModel):
    pins: list[BoardPinOut]
    has_more: bool


@router.post("/board-list", response_model=BoardListOut)
def board_list(
    body: BoardListBody,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> BoardListOut:
    listing = list_board(db, user, body.url)
    return BoardListOut(
        pins=[
            BoardPinOut(
                pin_id=pin.pin_id,
                pin_url=pin.pin_url,
                thumbnail_url=pin.thumbnail_url,
                has_video=pin.has_video,
                title=pin.title,
            )
            for pin in listing.pins
        ],
        has_more=listing.has_more,
    )
