"""Autenticação por código enviado ao e-mail e sessão server-side.

Fluxo: request_login_code -> verify_login_code -> authenticate_session / revoke_session.
As rotas só traduzem HTTP; o banco só guarda dados; as regras ficam aqui.

Garantias importantes:
- Um único código aberto por e-mail (UNIQUE(email, active_slot) no banco).
- A tentativa é contada de forma atômica ANTES de comparar o código.
- Só uma verificação consegue fechar o código aberto: as demais recebem "inválido".
- O usuário só é criado depois do código correto.
- A sessão expira na data original (não é renovada a cada requisição).
"""
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from config import SESSION_LAST_USED_UPDATE_INTERVAL_SECONDS, Settings, get_settings
from database.models import AuthSession, LoginCode, User
from database.models.user import normalize_email
from database.types import utcnow
from services.mailer import EmailMessage, EmailSender
from utils.security import (
    constant_time_equals,
    generate_login_code,
    generate_session_token,
    hash_ip,
    hash_login_code,
    hash_session_token,
    is_valid_code_format,
    is_valid_email_format,
    mask_email,
)

logger = logging.getLogger("minhoca")

_NO_CODE_HASH = "0" * 64  # comparado quando não há código aberto (tempo de resposta parecido)


# ------------------------------------------------------------------------------ erros
class AuthError(Exception):
    status_code = 400
    code = "auth_error"
    detail = "Não foi possível concluir a operação."

    def __init__(self, detail: str | None = None, *, retry_after: int | None = None) -> None:
        self.detail = detail or self.detail
        self.retry_after = retry_after
        super().__init__(self.detail)


class InvalidEmailError(AuthError):
    status_code = 400
    code = "invalid_email"
    detail = "Informe um e-mail válido."


class RateLimitedError(AuthError):
    status_code = 429
    code = "rate_limited"
    detail = "Muitas solicitações. Aguarde um pouco e tente novamente."


class InvalidCodeError(AuthError):
    """Uma única mensagem para código errado, expirado, esgotado ou inexistente."""

    status_code = 400
    code = "invalid_code"
    detail = "Código inválido ou expirado. Solicite um novo código."


class UnauthenticatedError(AuthError):
    status_code = 401
    code = "not_authenticated"
    detail = "Sessão inválida ou expirada."


class EmailUnavailableError(AuthError):
    status_code = 503
    code = "email_unavailable"
    detail = "Não foi possível enviar o código agora. Tente novamente em instantes."


# ------------------------------------------------------------------------------ resultados
@dataclass(frozen=True)
class CodeIssued:
    expires_in: int
    resend_after: int


@dataclass(frozen=True)
class LoginResult:
    user: User
    token: str
    expires_at: datetime


# ------------------------------------------------------------------------------ pedir código
def request_login_code(
    db: Session,
    *,
    email: str,
    ip: str | None,
    sender: EmailSender,
    cfg: Settings | None = None,
) -> CodeIssued:
    cfg = cfg or get_settings()
    email = normalize_email(email) if isinstance(email, str) else ""
    if not is_valid_email_format(email):
        raise InvalidEmailError()

    now = utcnow()
    ip_hash = hash_ip(ip, cfg.ip_hash_secret) if ip else None
    _enforce_send_limits(db, email=email, ip_hash=ip_hash, now=now, cfg=cfg)

    code = generate_login_code()
    try:
        # Fecha o código aberto anterior (se houver) e abre o novo, na mesma transação.
        db.execute(
            update(LoginCode)
            .where(LoginCode.email == email, LoginCode.active_slot == 1)
            .values(closed_at=now, close_reason="superseded", active_slot=None)
        )
        row = LoginCode(
            email=email,
            code_hash=hash_login_code(email, code, cfg.auth_secret_key),
            attempts=0,
            expires_at=now + timedelta(seconds=cfg.login_code_ttl_seconds),
            active_slot=1,
            ip_hash=ip_hash,
            created_at=now,
        )
        db.add(row)
        db.commit()
    except IntegrityError:
        # Outro pedido para o mesmo e-mail abriu um código no mesmo instante (UNIQUE do banco).
        db.rollback()
        raise RateLimitedError(
            "Um código acabou de ser enviado. Aguarde antes de pedir outro.",
            retry_after=max(1, cfg.login_code_min_interval_seconds),
        ) from None
    code_id = row.id

    try:
        sender.send(_login_code_message(email, code, cfg))
    except Exception as exc:  # qualquer falha do sender vira 503 genérico (o motivo real vai só para o log)
        logger.error("[AUTH] falha ao enviar código para %s: %s", mask_email(email), exc.__class__.__name__)
        _close_code(db, code_id, reason="send_failed")
        raise EmailUnavailableError() from None

    logger.info("[AUTH] código enviado para %s", mask_email(email))
    return CodeIssued(
        expires_in=cfg.login_code_ttl_seconds,
        resend_after=cfg.login_code_min_interval_seconds,
    )


def _login_code_message(email: str, code: str, cfg: Settings) -> EmailMessage:
    minutes = max(1, cfg.login_code_ttl_seconds // 60)
    return EmailMessage(
        to=email,
        subject="Seu código de acesso ao Minhoca de Jardim",
        text=(
            "Seu código de acesso ao Minhoca de Jardim:\n\n"
            f"    {code}\n\n"
            f"Ele vale por {minutes} minuto(s) e só pode ser usado uma vez.\n"
            "Se você não pediu este código, ignore este e-mail.\n"
        ),
    )


def _close_code(db: Session, code_id: int, *, reason: str) -> int:
    result = db.execute(
        update(LoginCode)
        .where(LoginCode.id == code_id, LoginCode.active_slot == 1)
        .values(closed_at=utcnow(), close_reason=reason, active_slot=None)
    )
    db.commit()
    return result.rowcount


def _retry_after(seconds: float) -> int:
    return max(1, math.ceil(seconds))


def _enforce_send_limits(db: Session, *, email: str, ip_hash: str | None, now: datetime, cfg: Settings) -> None:
    """Limites de envio, calculados a partir da própria tabela login_codes.

    Códigos com falha de envio (`send_failed`) não contam: nenhum e-mail chegou a ser entregue.
    """
    window_start = now - timedelta(hours=1)
    delivered = or_(LoginCode.close_reason.is_(None), LoginCode.close_reason != "send_failed")

    by_email = (
        db.execute(
            select(LoginCode.created_at)
            .where(LoginCode.email == email, LoginCode.created_at > window_start, delivered)
            .order_by(LoginCode.created_at.desc())
        )
        .scalars()
        .all()
    )
    if by_email:
        elapsed = (now - by_email[0]).total_seconds()
        if elapsed < cfg.login_code_min_interval_seconds:
            raise RateLimitedError(
                "Aguarde um instante antes de pedir outro código.",
                retry_after=_retry_after(cfg.login_code_min_interval_seconds - elapsed),
            )
    if len(by_email) >= cfg.login_code_max_per_email_per_hour:
        frees_at = by_email[cfg.login_code_max_per_email_per_hour - 1] + timedelta(hours=1)
        raise RateLimitedError(
            "Limite de códigos por hora atingido. Tente novamente mais tarde.",
            retry_after=_retry_after((frees_at - now).total_seconds()),
        )

    if ip_hash:
        by_ip = (
            db.execute(
                select(LoginCode.created_at)
                .where(LoginCode.ip_hash == ip_hash, LoginCode.created_at > window_start, delivered)
                .order_by(LoginCode.created_at.desc())
            )
            .scalars()
            .all()
        )
        if len(by_ip) >= cfg.login_code_max_per_ip_per_hour:
            frees_at = by_ip[cfg.login_code_max_per_ip_per_hour - 1] + timedelta(hours=1)
            raise RateLimitedError(
                "Muitas solicitações a partir desta rede. Tente novamente mais tarde.",
                retry_after=_retry_after((frees_at - now).total_seconds()),
            )


# ------------------------------------------------------------------------------ verificar código
def verify_login_code(db: Session, *, email: str, code: str, cfg: Settings | None = None) -> LoginResult:
    cfg = cfg or get_settings()
    email = normalize_email(email) if isinstance(email, str) else ""
    if not is_valid_email_format(email):
        raise InvalidEmailError()
    if not is_valid_code_format(code):
        raise InvalidCodeError()

    now = utcnow()
    candidate = hash_login_code(email, code, cfg.auth_secret_key)
    open_code = db.execute(
        select(LoginCode.id, LoginCode.code_hash).where(LoginCode.email == email, LoginCode.active_slot == 1)
    ).first()
    if open_code is None:
        constant_time_equals(candidate, _NO_CODE_HASH)
        raise InvalidCodeError()

    # A tentativa é contada de forma atômica ANTES de comparar. Se o código expirou ou já gastou
    # todas as tentativas, nenhuma linha é afetada e a resposta é a mesma de um código errado.
    counted = db.execute(
        update(LoginCode)
        .where(
            LoginCode.id == open_code.id,
            LoginCode.active_slot == 1,
            LoginCode.attempts < cfg.login_code_max_attempts,
            LoginCode.expires_at > now,
        )
        .values(attempts=LoginCode.attempts + 1)
    )
    db.commit()
    if counted.rowcount != 1:
        raise InvalidCodeError()

    if not constant_time_equals(open_code.code_hash, candidate):
        # Se essa foi a última tentativa, o código é queimado: só um novo código resolve.
        db.execute(
            update(LoginCode)
            .where(
                LoginCode.id == open_code.id,
                LoginCode.active_slot == 1,
                LoginCode.attempts >= cfg.login_code_max_attempts,
            )
            .values(closed_at=now, close_reason="exhausted", active_slot=None)
        )
        db.commit()
        raise InvalidCodeError()

    # Código correto: fecha o código, cria/atualiza o usuário e cria a sessão numa única transação.
    for attempt in (1, 2):
        try:
            closed = db.execute(
                update(LoginCode)
                .where(LoginCode.id == open_code.id, LoginCode.active_slot == 1)
                .values(closed_at=now, close_reason="verified", active_slot=None)
            )
            if closed.rowcount != 1:  # outra verificação já usou este código
                db.rollback()
                raise InvalidCodeError()
            user = _get_or_create_verified_user(db, email, now)
            token, expires_at = _create_session(db, user, now, cfg)
            db.commit()
            logger.info("[AUTH] login confirmado para %s", mask_email(email))
            return LoginResult(user=user, token=token, expires_at=expires_at)
        except IntegrityError:
            # Corrida rara na criação do usuário: refaz uma vez (o usuário já existe na segunda tentativa).
            db.rollback()
            if attempt == 2:
                raise
    raise InvalidCodeError()  # inalcançável; deixa explícito para quem lê


def _get_or_create_verified_user(db: Session, email: str, now: datetime) -> User:
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if user is None:
        user = User(email=email, email_verified_at=now)
        db.add(user)
        db.flush()
    elif user.email_verified_at is None:
        user.email_verified_at = now
    return user


def _create_session(db: Session, user: User, now: datetime, cfg: Settings) -> tuple[str, datetime]:
    token = generate_session_token()
    expires_at = now + timedelta(days=cfg.session_ttl_days)
    db.add(
        AuthSession(
            user_id=user.id,
            token_hash=hash_session_token(token),
            created_at=now,
            last_used_at=now,
            expires_at=expires_at,
        )
    )
    db.flush()
    return token, expires_at


# ------------------------------------------------------------------------------ sessão
def authenticate_session(db: Session, token: str | None) -> User | None:
    """Devolve o usuário da sessão, ou None. NÃO renova a validade (30 dias fixos)."""
    if not token or len(token) > 512:
        return None
    found = db.execute(
        select(AuthSession, User)
        .join(User, User.id == AuthSession.user_id)
        .where(AuthSession.token_hash == hash_session_token(token))
    ).first()
    if found is None:
        return None
    auth_session, user = found
    now = utcnow()
    if auth_session.revoked_at is not None or auth_session.expires_at <= now:
        return None
    if (now - auth_session.last_used_at).total_seconds() >= SESSION_LAST_USED_UPDATE_INTERVAL_SECONDS:
        db.execute(
            update(AuthSession)
            .where(AuthSession.id == auth_session.id, AuthSession.revoked_at.is_(None))
            .values(last_used_at=now)
        )
        db.commit()
    return user


def revoke_session(db: Session, token: str | None) -> bool:
    """Encerra a sessão (logout). Repetir a chamada é seguro: devolve False se já estava encerrada."""
    if not token or len(token) > 512:
        return False
    result = db.execute(
        update(AuthSession)
        .where(AuthSession.token_hash == hash_session_token(token), AuthSession.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )
    db.commit()
    return result.rowcount == 1
