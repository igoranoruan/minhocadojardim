"""Funções de segurança da autenticação (puras: sem banco e sem HTTP).

- Código de login: 6 dígitos gerados com `secrets`, guardados como HMAC-SHA256 com segredo do servidor.
  (Um código de 6 dígitos cai em segundos num ataque offline, seja qual for o hash; a defesa real é o
  segredo fora do banco + validade curta + limite de tentativas.)
- Token de sessão: 256 bits aleatórios; o banco guarda só o SHA-256 (o token já tem alta entropia).
- IP: guardado apenas como HMAC com segredo separado, só para limitar abuso. Isso NÃO garante, por si só,
  que o dado deixe de ser pessoal para fins de LGPD (tratamento jurídico fica para etapa própria).
"""
import hashlib
import hmac
import re
import secrets

from config import LOGIN_CODE_LENGTH, SESSION_TOKEN_BYTES

_EMAIL_RE = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")
_CODE_RE = re.compile(rf"[0-9]{{{LOGIN_CODE_LENGTH}}}")


def generate_login_code() -> str:
    """Código numérico com zeros à esquerda (sempre string), ex.: '000042'."""
    return f"{secrets.randbelow(10 ** LOGIN_CODE_LENGTH):0{LOGIN_CODE_LENGTH}d}"


def is_valid_code_format(code) -> bool:
    return isinstance(code, str) and _CODE_RE.fullmatch(code) is not None


def _hmac_hex(secret: str, label: str, *parts: str) -> str:
    message = ":".join((label, *parts)).encode("utf-8")
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def hash_login_code(email: str, code: str, secret: str) -> str:
    """HMAC-SHA256 (64 hex) do código, amarrado ao e-mail."""
    return _hmac_hex(secret, "login-code", email, code)


def hash_ip(ip: str, secret: str) -> str:
    """HMAC-SHA256 (64 hex) do IP, com o segredo próprio de IP."""
    return _hmac_hex(secret, "ip", ip)


def generate_session_token() -> str:
    return secrets.token_urlsafe(SESSION_TOKEN_BYTES)


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def is_valid_email_format(email: str) -> bool:
    """Validação de formato (não prova que o e-mail existe: quem prova é o código enviado)."""
    return (
        isinstance(email, str)
        and 0 < len(email) <= 320
        and email.count("@") == 1
        and email.isprintable()
        and _EMAIL_RE.fullmatch(email) is not None
    )


def mask_email(email: str) -> str:
    """'ana@exemplo.com' -> 'a***@e***' (para logs: nunca registrar o e-mail completo)."""
    local, _, domain = email.partition("@")
    return f"{local[:1]}***@{domain[:1]}***"
