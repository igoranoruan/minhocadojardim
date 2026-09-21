"""Rotas de autenticação: e-mail -> código -> sessão em cookie.

POST /api/auth/request-code   pede o código (resposta igual exista o usuário ou não)
POST /api/auth/verify-code    confirma o código e abre a sessão (cookie HttpOnly)
GET  /api/auth/me             quem está logado
POST /api/auth/logout         encerra a sessão (repetir é seguro)

Toda resposta daqui leva Cache-Control: no-store. Erros: {"detail": "...", "code": "..."}.
A checagem de Origin é feita uma vez só, pelo middleware (utils/origin.py).
"""
from fastapi import APIRouter, Depends, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from config import Settings, get_settings
from database.models import User
from database.session import get_session
from routes.deps import client_ip, get_current_user, get_email_sender
from services.auth import AuthError, request_login_code, revoke_session, verify_login_code
from services.mailer import EmailSender

router = APIRouter(prefix="/api/auth", tags=["auth"])

NO_STORE = {"Cache-Control": "no-store"}


class RequestCodeBody(BaseModel):
    email: str = Field(max_length=320)


class VerifyCodeBody(BaseModel):
    email: str = Field(max_length=320)
    code: str = Field(max_length=32)


async def auth_error_handler(request: Request, exc: AuthError) -> JSONResponse:
    headers = dict(NO_STORE)
    if exc.retry_after is not None:
        headers["Retry-After"] = str(exc.retry_after)
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "code": exc.code},
        headers=headers,
    )


async def validation_error_handler(request: Request, exc: RequestValidationError):
    """Nas rotas de auth, corpo inválido segue o mesmo formato de erro; nas demais, o padrão do FastAPI."""
    if request.url.path.startswith("/api/auth/"):
        return JSONResponse(
            status_code=400,
            content={"detail": "Requisição inválida.", "code": "invalid_request"},
            headers=NO_STORE,
        )
    return await request_validation_exception_handler(request, exc)


def _cookie_options(cfg: Settings) -> dict:
    return {
        "path": "/",
        "httponly": True,
        "secure": cfg.session_cookie_secure,
        "samesite": "lax",
    }


@router.post("/request-code")
def request_code(
    body: RequestCodeBody,
    request: Request,
    db: Session = Depends(get_session),
    sender: EmailSender = Depends(get_email_sender),
    cfg: Settings = Depends(get_settings),
) -> JSONResponse:
    issued = request_login_code(db, email=body.email, ip=client_ip(request), sender=sender, cfg=cfg)
    return JSONResponse(
        {"status": "sent", "expires_in": issued.expires_in, "resend_after": issued.resend_after},
        headers=NO_STORE,
    )


@router.post("/verify-code")
def verify_code(
    body: VerifyCodeBody,
    db: Session = Depends(get_session),
    cfg: Settings = Depends(get_settings),
) -> JSONResponse:
    result = verify_login_code(db, email=body.email, code=body.code, cfg=cfg)
    response = JSONResponse({"email": result.user.email}, headers=NO_STORE)
    response.set_cookie(
        key=cfg.session_cookie_name,
        value=result.token,
        max_age=cfg.session_ttl_days * 86400,
        **_cookie_options(cfg),
    )
    return response


@router.get("/me")
def me(user: User = Depends(get_current_user)) -> JSONResponse:
    return JSONResponse(
        {"email": user.email, "email_verified": user.email_verified_at is not None},
        headers=NO_STORE,
    )


@router.post("/logout")
def logout(
    request: Request,
    db: Session = Depends(get_session),
    cfg: Settings = Depends(get_settings),
) -> JSONResponse:
    revoke_session(db, request.cookies.get(cfg.session_cookie_name))
    response = JSONResponse({"status": "ok"}, headers=NO_STORE)
    response.delete_cookie(key=cfg.session_cookie_name, **_cookie_options(cfg))
    return response
