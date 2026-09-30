"""Grava o cookie de identidade anônima na resposta HTTP (Free sem login -- aprovação do CÉREBRO).

Por que um middleware, e não `response.set_cookie()` dentro da dependência: rotas de geração
(routes/generations.py) devolvem seu PRÓPRIO objeto Response (StreamingResponse/FileResponse),
construído dentro da própria função da rota -- o `Response` que o FastAPI injeta numa dependência
via `Depends` só é mesclado na resposta final quando a rota devolve um valor simples (dict/
pydantic) que o FastAPI empacota no `response_class` declarado; quando a rota devolve seu próprio
objeto Response diretamente (como aqui), esse mecanismo NÃO se aplica (documentado pelo próprio
FastAPI). A única forma confiável de garantir o header `Set-Cookie` em QUALQUER tipo de resposta
(incluindo streaming) é um middleware que envolve a resposta já pronta -- mesmo padrão já usado
neste projeto por utils/origin.py::OriginProtectionMiddleware (BaseHTTPMiddleware; `call_next`
devolve o objeto de resposta real, cujo `.headers`/`.set_cookie()` afeta o que é enviado ao
navegador, qualquer que seja o tipo de Response da rota).

routes/deps.py::get_generation_user grava o token (só quando uma identidade NOVA é criada) em
`request.state.anon_cookie_token` -- este middleware só lê esse valor depois que a resposta já
existe e grava o cookie nela.
"""
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from config import get_settings


class AnonymousCookieMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        token = getattr(request.state, "anon_cookie_token", None)
        if token:
            cfg = get_settings()
            response.set_cookie(
                key=cfg.anon_cookie_name,
                value=token,
                max_age=cfg.anon_cookie_ttl_days * 86400,
                path="/",
                httponly=True,
                secure=cfg.anon_cookie_secure,
                samesite="lax",
            )
        return response
