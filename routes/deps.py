"""Dependências compartilhadas das rotas (sessão do usuário, sender de e-mail, IP)."""
import logging

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from config import Settings, get_settings
from database.models import User
from database.session import get_session
from services.anon_identity import resolve_anon_identity
from services.auth import ForbiddenError, UnauthenticatedError, authenticate_session
from services.entitlements import get_current_entitlement
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
    """Usuário para fins de GERAÇÃO/COTA: sessão autenticada COM plano pago vigente, OU a
    identidade Free do dispositivo (Free sem login -- aprovação do CÉREBRO, "Free anônimo").
    NUNCA lança 401: sempre devolve um User utilizável, criando um usuário-dispositivo na primeira
    visita, se preciso.

    Correção do loophole "Free reseta no logout" (aprovação do CÉREBRO): sessão autenticada só
    vence quando o usuário tem um `entitlement` pago vigente (get_current_entitlement). Sessão
    autenticada SEM plano pago cai para a MESMA identidade Free do dispositivo usada por um
    visitante sem login -- é assim que a cota Free sobrevive a login/logout/troca de e-mail no
    mesmo navegador: a conta autenticada e a identidade Free do dispositivo são SEMPRE duas linhas
    separadas em `users` (nunca uma vira a outra -- ver services/anon_identity.py), e a escolha de
    qual delas usar é só esta função, refeita a cada requisição a partir do estado real (sessão +
    entitlement), nunca guardada/lembrada em outro lugar.

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
    if session_user is not None and get_current_entitlement(db, session_user.id) is not None:
        return session_user

    result = resolve_anon_identity(db, device_token=request.cookies.get(cfg.anon_cookie_name))
    if result.created and result.token:
        request.state.anon_cookie_token = result.token
    return result.user


def get_optional_authenticated_user(
    request: Request,
    db: Session = Depends(get_session),
    cfg: Settings = Depends(get_settings),
) -> User | None:
    """Sessão autenticada, se houver -- ou None. NUNCA lança 401, NUNCA resolve/cria identidade
    anônima (ao contrário de get_generation_user): só verifica se HÁ uma sessão de verdade.

    Correção de ownership no download (aprovação do CÉREBRO): desde que get_generation_user passou
    a usar a identidade Free do dispositivo para sessão SEM plano pago, uma geração antiga
    pertencente à própria conta autenticada (criada antes desta correção, ou enquanto o plano
    ainda era pago) deixaria de ser reconhecida como "do usuário" -- o dono de fato
    (`generation.user_id`) é a conta autenticada, não o dispositivo. Esta dependência existe só
    para a rota de download poder considerar TAMBÉM a conta autenticada como possível dona, além da
    identidade efetiva de get_generation_user (ver services/usage.py::get_downloadable_generation,
    parâmetro `authenticated_user_id`) -- nunca o contrário: o cookie Free sozinho continua
    incapaz de baixar uma geração de uma conta alheia, e continua incapaz de conceder acesso pago."""
    return authenticate_session(db, request.cookies.get(cfg.session_cookie_name))


def require_admin(
    user: User = Depends(get_current_user),
    cfg: Settings = Depends(get_settings),
) -> User:
    """Sessão autenticada CUJO e-mail é o configurado em ADMIN_EMAIL (05/10/2026, aprovação do
    CÉREBRO): único "admin" do produto hoje é o próprio CÉREBRO, sem cargo/permissão no banco. Sem
    sessão: 401 (via get_current_user). Com sessão mas e-mail diferente, ou ADMIN_EMAIL não
    configurado (string vazia nunca bate com e-mail real nenhum): 403 (ForbiddenError). Usado hoje
    só para remover comentário (routes/comments.py) -- nenhum outro recurso administrativo existe
    ainda."""
    if not cfg.admin_email or user.email != cfg.admin_email:
        raise ForbiddenError()
    return user
