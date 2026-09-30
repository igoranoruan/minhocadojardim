"""Dependências compartilhadas das rotas (sessão do usuário, sender de e-mail, IP)."""
import logging

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from config import Settings, get_settings
from database.models import User
from database.session import get_session
from services.anon_identity import resolve_anon_identity
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


def get_generation_user(
    request: Request,
    db: Session = Depends(get_session),
    cfg: Settings = Depends(get_settings),
) -> User:
    """Usuário para fins de GERAÇÃO/COTA: sessão autenticada OU identidade anônima (Free sem
    login -- aprovação do CÉREBRO, "Free anônimo"). Sessão autenticada SEMPRE vence -- só resolve
    identidade anônima quando não há sessão válida. NUNCA lança 401: sempre devolve um User
    utilizável, criando um usuário-dispositivo na primeira visita, se preciso.

    Diferente de get_current_user (que continua significando só "sessão autenticada", inalterada,
    e usada em pagamentos/lote/`/api/auth/me`): esta dependência só deve ser usada nas rotas
    explicitamente abertas ao Free anônimo (criação/download de geração individual e `/api/me/status`).

    Quando um usuário-dispositivo NOVO é criado, o token do cookie fica em
    `request.state.anon_cookie_token` -- não é setado aqui porque um Response injetado por
    dependência não é mesclado na resposta quando a rota devolve seu PRÓPRIO objeto Response
    (StreamingResponse/FileResponse), como é o caso das rotas de geração. Quem efetivamente grava
    o cookie na resposta HTTP é o AnonymousCookieMiddleware (utils/anon_cookie.py), que lê este
    mesmo `request.state` depois que a resposta já foi construída -- funciona para qualquer tipo
    de Response, streaming incluído (ver o teste HTTP real em tests/test_anon_identity.py)."""
    session_user = authenticate_session(db, request.cookies.get(cfg.session_cookie_name))
    if session_user is not None:
        return session_user

    result = resolve_anon_identity(db, device_token=request.cookies.get(cfg.anon_cookie_name))
    if result.created and result.token:
        request.state.anon_cookie_token = result.token
    return result.user
