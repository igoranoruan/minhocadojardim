"""Dependências compartilhadas das rotas (sessão do usuário, sender de e-mail, IP)."""
import logging

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from config import Settings, get_settings
from database.models import User
from database.session import get_session
from services.auth import UnauthenticatedError, authenticate_session
from services.mailer import (
    EmailConfigurationError,
    EmailSender,
    UnavailableEmailSender,
    build_email_sender,
)

logger = logging.getLogger("minhoca")


def get_email_sender(cfg: Settings = Depends(get_settings)) -> EmailSender:
    """Sender do ambiente. Se não houver um válido, devolve um que falha (a rota responde 503)."""
    try:
        return build_email_sender(cfg)
    except EmailConfigurationError as exc:
        logger.error("[EMAIL] %s", exc)
        return UnavailableEmailSender(str(exc))


def client_ip(request: Request) -> str | None:
    """IP de quem chamou. Atrás do proxy do Render isto exige configuração (checklist da Etapa 18)."""
    return request.client.host if request.client else None


def get_current_user(
    request: Request,
    db: Session = Depends(get_session),
    cfg: Settings = Depends(get_settings),
) -> User:
    """Usuário da sessão (cookie). Base das rotas de gerações, pagamentos e planos.

    A identidade NUNCA vem do corpo, da URL nem de e-mail informado pelo frontend.
    """
    user = authenticate_session(db, request.cookies.get(cfg.session_cookie_name))
    if user is None:
        raise UnauthenticatedError()
    return user
