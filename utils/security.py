"""Funções de segurança da autenticação (puras: sem banco e sem HTTP).

- Código de login: 6 dígitos gerados com `secrets`, guardados como HMAC-SHA256 com segredo do servidor.
  (Um código de 6 dígitos cai em segundos num ataque offline, seja qual for o hash; a defesa real é o
  segredo fora do banco + validade curta + limite de tentativas.)
- Token de sessão: 256 bits aleatórios; o banco guarda só o SHA-256 (o token já tem alta entropia).
- Token de identidade anônima (Free sem login): mesmo padrão do token de sessão -- 256 bits
  aleatórios, banco guarda só o SHA-256, sem segredo adicional (a entropia do token já basta).
- IP: guardado apenas como HMAC com segredo separado, só para limitar abuso. Isso NÃO garante, por si só,
  que o dado deixe de ser pessoal para fins de LGPD (tratamento jurídico fica para etapa própria).
"""
import hashlib
import hmac
import re
import secrets

from config import ANON_TOKEN_BYTES, LOGIN_CODE_LENGTH, SESSION_TOKEN_BYTES, SHARE_TOKEN_BYTES

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


def hash_device_id(device_id: str, secret: str) -> str:
    """HMAC-SHA256 (64 hex) do identificador de aparelho (ETAPA B7, teste VIP de 24h) -- MESMO
    segredo de hash_ip (settings.ip_hash_secret: já existe, já é de uso exclusivamente interno,
    nenhum segredo novo precisa ser provisionado), mas rótulo PRÓPRIO ("device-id" em vez de "ip")
    -- os dois HMACs nunca colidem, mesmo que, por coincidência, IP e device_id crus fossem o
    mesmo texto. O valor cru (gerado pelo FRONTEND, guardado em localStorage/cookie) nunca chega a
    ser persistido; só este hash, em database/models/trial_redemption.py."""
    return _hmac_hex(secret, "device-id", device_id)


def generate_session_token() -> str:
    return secrets.token_urlsafe(SESSION_TOKEN_BYTES)


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_anon_device_token() -> str:
    """Token do cookie de identidade anônima (mesma entropia do token de sessão)."""
    return secrets.token_urlsafe(ANON_TOKEN_BYTES)


def hash_anon_device_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


# Token do atalho "Enviar para o KLANGO" (15/10/2026, aprovação do CÉREBRO): MESMA entropia e MESMO
# padrão do token de sessão/anônimo (256 bits, secrets.token_urlsafe, banco guarda só o SHA-256) --
# mas com função PRÓPRIA, nunca hash_session_token, para manter os dois namespaces de token sem
# nenhuma possibilidade de confusão entre eles (um token de sessão nunca deve, por acidente de
# código, autenticar a rota do atalho, e vice-versa).
def generate_share_token() -> str:
    return secrets.token_urlsafe(SHARE_TOKEN_BYTES)


def hash_share_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# Alfabeto do código de indicação (Etapa 8, programa de indicação): só maiúsculas e dígitos, SEM
# os caracteres classicamente confundíveis ao digitar/ler em voz alta -- 0/O, 1/I/L -- já que este
# código (diferente do token de sessão/anônimo, acima) é feito para ser DIGITADO ou lido por uma
# pessoa, não só colado de um link. Não precisa da imprevisibilidade de um segredo (não protege
# nada sensível: na pior hipótese, alguém usa o código de outra pessoa como indicador, o que só
# soma dias de plano a quem já tinha o código -- nunca concede acesso nem dinheiro a quem adivinha).
_REFERRAL_CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"


def generate_referral_code(length: int) -> str:
    """Código aleatório (não precisa ser único por si só -- a unicidade é responsabilidade de quem
    grava em users.referral_code, com retry em caso de colisão; ver services/referrals.py)."""
    return "".join(secrets.choice(_REFERRAL_CODE_ALPHABET) for _ in range(length))


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
