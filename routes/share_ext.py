"""Rota ÚNICA do atalho de compartilhamento (iPhone/Android) -- POST /api/ext/share.

Prefixo PRÓPRIO ("/api/ext/", separado de "/api/share/..." em routes/share.py) de propósito: é o
único jeito de isentar ESTA rota do OriginProtectionMiddleware (config.ORIGIN_CHECK_EXEMPT_PREFIXES)
sem isentar junto as rotas de /api/share/... que usam cookie -- ver o comentário em config.py ao
lado de ORIGIN_CHECK_EXEMPT_PREFIXES.

Autenticação EXCLUSIVAMENTE por token pessoal (header "Authorization: Bearer <token>" OU campo
"token" no corpo, para compatibilidade com o app Atalhos do iOS) -- esta rota NUNCA lê
`request.cookies`, mesmo que um cookie de sessão venha na requisição (navegador embutido de algum
cliente, por exemplo). Não usa routes/deps.py::get_current_user nem authenticate_session (que
LEEM cookie) -- a identidade vem exclusivamente de services.share_queue.authenticate_share_token,
chamada com o token extraído do header/corpo. `Depends(get_session)` continua usado normalmente
(é só a Session do banco -- nunca olha request.cookies -- e é o que permite os testes trocarem o
banco via app.dependency_overrides, mesmo padrão de toda outra rota do projeto).

Resposta: TEXTO PURO (Content-Type: text/plain), pronto para o passo "Mostrar Notificação" do
Atalhos -- nunca JSON, nunca HTTP de erro para um resultado "recuperável" (link duplicado, fila
cheia, link não suportado): isso sempre volta 200 com uma mensagem amigável (pedido explícito do
CÉREBRO -- o Atalhos trata uma resposta HTTP de erro de forma menos amigável que um 200 com texto
de aviso). SÓ token ausente/inválido/revogado vira 401 de verdade (ver
share_token_error_handler, registrado em main.py)."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database.session import get_session
from services.share_queue import (
    InvalidShareTokenError,
    add_to_queue,
    authenticate_share_token,
    extract_token_from_request,
)

router = APIRouter(prefix="/api/ext", tags=["share-ext"])

NO_STORE = {"Cache-Control": "no-store"}


async def share_token_error_handler(request: Request, exc: InvalidShareTokenError) -> JSONResponse:
    """Único caso desta rota que É um erro HTTP de verdade (token ausente/inválido/revogado) --
    tudo o mais (link duplicado, fila cheia, link não suportado) é um 200 com texto amigável,
    nunca passa por aqui (ver docstring do módulo)."""
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "code": "invalid_share_token"},
        headers=NO_STORE,
    )


class ShareExtBody(BaseModel):
    url: str | None = Field(default=None, max_length=2048)
    text: str | None = Field(default=None, max_length=4096)
    # Só para compatibilidade com o Atalhos (o header Authorization é o caminho preferido) -- ver
    # services.share_queue.extract_token_from_request.
    token: str | None = Field(default=None, max_length=512)


@router.post("/share", response_class=PlainTextResponse)
def post_share(body: ShareExtBody, request: Request, db: Session = Depends(get_session)) -> PlainTextResponse:
    token = extract_token_from_request(authorization_header=request.headers.get("authorization"), body_token=body.token)
    user = authenticate_share_token(db, token)
    if user is None:
        raise InvalidShareTokenError()
    resultado = add_to_queue(db, user, url=body.url, text=body.text)
    return PlainTextResponse(resultado.message)
