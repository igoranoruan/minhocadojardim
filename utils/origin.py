"""Proteção centralizada de Origin (CSRF) para requisições que alteram dados.

Como a sessão vai em cookie, toda requisição não segura (POST, PUT, PATCH, DELETE) precisa vir
da nossa própria origem (PUBLIC_BASE_URL). Preferimos o cabeçalho Origin; se ele não existir,
usamos o Referer. Sem nenhum dos dois, a requisição é recusada.

A regra vive num único lugar (este middleware) e vale para TODAS as rotas: uma rota nova
nasce protegida. Só ficam de fora os prefixos de ORIGIN_CHECK_EXEMPT_PREFIXES (webhooks do
Mercado Pago, que são servidor-a-servidor e não usam cookie).
"""
import logging
from urllib.parse import urlsplit

from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from config import ORIGIN_CHECK_EXEMPT_PREFIXES, get_settings

logger = logging.getLogger("minhoca")

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_DEFAULT_PORTS = {"http": 80, "https": 443}


def origin_of(url: str) -> str | None:
    """'https://Exemplo.com:443/x' -> 'https://exemplo.com:443' (None se não for http/https válido)."""
    try:
        parts = urlsplit(url.strip())
        scheme = parts.scheme.lower()
        host = parts.hostname
        if scheme not in _DEFAULT_PORTS or not host:
            return None
        port = parts.port or _DEFAULT_PORTS[scheme]
    except ValueError:
        return None
    return f"{scheme}://{host.lower()}:{port}"


def is_origin_allowed(
    *,
    method: str,
    path: str,
    origin: str | None,
    referer: str | None,
    base_url: str,
    exempt_prefixes: tuple[str, ...] = ORIGIN_CHECK_EXEMPT_PREFIXES,
) -> bool:
    if method.upper() in SAFE_METHODS:
        return True
    if any(path.startswith(prefix) for prefix in exempt_prefixes):
        return True
    allowed = origin_of(base_url)
    if allowed is None:
        return False
    if origin is not None:  # Origin tem prioridade; "null" e valores estranhos são recusados
        return origin_of(origin) == allowed
    if referer:
        return origin_of(referer) == allowed
    return False


class OriginProtectionMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        cfg = get_settings()
        if not is_origin_allowed(
            method=request.method,
            path=request.url.path,
            origin=request.headers.get("origin"),
            referer=request.headers.get("referer"),
            base_url=cfg.public_base_url,
        ):
            logger.warning("[AUTH] origem recusada: %s %s", request.method, request.url.path)
            return JSONResponse(
                status_code=403,
                content={"detail": "Origem da requisição não permitida.", "code": "invalid_origin"},
                headers={"Cache-Control": "no-store"},
            )
        return await call_next(request)
