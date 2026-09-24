"""Configuração central do Minhoca de Jardim.

Só entra aqui o que já é usado (ambiente, log, URL pública, fuso horário, banco e autenticação).
Limites de planos, arquivos e concorrência entram nas etapas em que forem usados.
Nenhum secret fica neste arquivo: tudo vem de variáveis de ambiente.
"""
import os
from dataclasses import dataclass, field

APP_NAME = "Minhoca de Jardim"
APP_VERSION = "2.0.0-etapa1"

# Todo controle de semana/dia (limites Free, Semanal etc.) usa este fuso.
TIMEZONE = "America/Sao_Paulo"

# Banco de desenvolvimento (SQLite local, pasta ignorada pelo Git).
DEFAULT_DATABASE_URL = "sqlite:///./data/minhoca.db"

# Tempo máximo (ms) que o SQLite espera por um lock de escrita antes de dar erro.
SQLITE_BUSY_TIMEOUT_MS = 15000

# ----------------------------------------------------------------------------- autenticação (Etapa 3)
LOGIN_CODE_LENGTH = 6  # dígitos do código enviado por e-mail
SESSION_TOKEN_BYTES = 32  # 256 bits de aleatoriedade no token da sessão
SESSION_COOKIE_NAME = "minhoca_session"
SESSION_COOKIE_NAME_PRODUCTION = "__Host-minhoca_session"  # __Host-: exige Secure, Path=/ e sem Domain
# last_used_at só é atualizado se estiver mais velho que isso (evita uma escrita por requisição).
SESSION_LAST_USED_UPDATE_INTERVAL_SECONDS = 3600
# Segredos: em produção são obrigatórios; em desenvolvimento usa-se um valor fixo e inseguro.
MIN_SECRET_LENGTH = 32
DEV_AUTH_SECRET_KEY = "dev-only-auth-secret-key-never-use-in-production-0001"
DEV_IP_HASH_SECRET = "dev-only-ip-hash-secret-never-use-in-production-0002"
# Rotas que NÃO passam pela checagem de Origin (servidor-a-servidor, sem cookie): webhooks futuros.
ORIGIN_CHECK_EXEMPT_PREFIXES = ("/api/webhooks/",)

# ----------------------------------------------------------------------------- planos e gerações (Etapa 4)
# O limite de vídeos por lote é definido por plano (Plan.max_batch_size em services/plans.py),
# não aqui: cada plano pago tem seu próprio teto (Etapa 4.1).
# Uma geração 'reserved' há mais tempo que isto deixa de contar na cota (rede de segurança contra
# reservas órfãs de um processamento que caiu): 15 vídeos (maior lote hoje) x 3 min de timeout + margem.
GENERATION_RESERVATION_TTL_SECONDS = 35 * 60

# ----------------------------------------------------------------------------- download (Etapa 5)
# 100 MB é o tamanho MAXIMO DO ARQUIVO EM DISCO. O download é feito em streaming (chunk a chunk) e
# abortado assim que ultrapassar este valor; o video nunca e carregado inteiro em memoria.
MAX_VIDEO_SIZE_BYTES = 100 * 1024 * 1024
# Tamanho de cada pedaco lido do socket por vez (streaming). Nao e o limite do video, e o "balde".
DOWNLOAD_CHUNK_SIZE_BYTES = 1024 * 1024
# yt-dlp as vezes sabe a duracao ANTES de baixar (permite recusar cedo). Quando nao sabe, o campo
# fica None: a validacao DEFINITIVA de duracao e da Etapa 6, quando FFmpeg/ffprobe entrar no projeto.
MAX_VIDEO_DURATION_SECONDS = 5 * 60
DOWNLOAD_TIMEOUT_SECONDS = 180
DOWNLOAD_CONNECT_TIMEOUT_SECONDS = 15
# Diretorio de trabalho dos downloads (fora de static/, ignorado pelo Git -- ver .gitignore: tmp/).
DOWNLOAD_TEMP_DIR = "./tmp/downloads"
# Salvaguarda contra redirecionamento infinito/abusivo nas checagens que a NOSSA camada resolve.
MAX_REDIRECTS = 5
# URL do PO Token Provider (BGUTIL), se o companion estiver rodando no ambiente. Vazio = o
# yt-dlp tenta o YouTube só com o player client mweb, sem PO Token (funciona para parte dos
# vídeos; especificação do produto: PO Token NAO garante todos os vídeos).
BGUTIL_POT_PROVIDER_BASE_URL = ""

# ----------------------------------------------------------------------------- processamento (Etapa 6)
# Reaproveita MAX_VIDEO_SIZE_BYTES (100 MB) e MAX_VIDEO_DURATION_SECONDS (5 min) definidas acima:
# são os MESMOS limites, agora validados de verdade no arquivo de entrada e no de saída via
# ffprobe (a Etapa 5 só media o que a plataforma informava, nem sempre confiável).
# "ffmpeg"/"ffprobe": nome de executável, resolvido em PATH via shutil.which() (processor/ffmpeg.py
# e processor/probe.py). Para apontar um binário específico, passe um caminho absoluto aqui —
# processor NUNCA assume um caminho fixo de Linux ou Windows.
FFMPEG_PATH = "ffmpeg"
FFPROBE_PATH = "ffprobe"
PROCESSING_TIMEOUT_SECONDS = 180
# Diretório de trabalho do processamento (fora de static/, ignorado pelo Git -- tmp/ no .gitignore).
PROCESSING_TEMP_DIR = "./tmp/processing"

# ----------------------------------------------------------------------------- storage do resultado (Etapa 8B.2)
# Diretório onde o MP4 final fica temporariamente disponível para download (ainda não implementado
# — isso é 8B.3). Precisa ficar no MESMO volume/filesystem que PROCESSING_TEMP_DIR: result_storage
# usa Path.rename() para mover o arquivo (sem copiar bytes), e rename só é atômico dentro do mesmo
# filesystem — os dois já vivem sob ./tmp/, então essa condição já é satisfeita.
RESULT_STORAGE_DIR = "./tmp/results"
# TTL do RESULTADO disponível para download — DELIBERADAMENTE separado de
# GENERATION_RESERVATION_TTL_SECONDS (que é sobre reservas "reserved" órfãs, um conceito
# diferente). A limpeza por TTL em si ainda não existe (isso é Etapa 8B.4); por enquanto só a
# constante e o cálculo de output_expires_at (finished_at + este TTL) existem.
RESULT_TTL_SECONDS = 30 * 60

def normalize_database_url(url: str) -> str:
    """Faz a URL no estilo Render/Heroku funcionar com o driver psycopg (v3).

    postgres://...   -> postgresql+psycopg://...
    postgresql://... -> postgresql+psycopg://...
    Qualquer outra URL (sqlite, ou já com driver) fica como está.
    """
    url = url.strip()
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def _int_env(name: str, default: int, minimum: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{name} deve ser um número inteiro (recebido: {raw!r})") from None
    if value < minimum:
        raise ValueError(f"{name} deve ser >= {minimum} (recebido: {value})")
    return value


@dataclass(frozen=True)
class Settings:
    env: str
    log_level: str
    public_base_url: str
    database_url: str
    db_pool_size: int
    db_max_overflow: int
    # Autenticação. Os segredos não aparecem no repr (evita vazar em logs/tracebacks).
    auth_secret_key: str = field(repr=False)
    ip_hash_secret: str = field(repr=False)
    email_sender: str
    login_code_ttl_seconds: int
    login_code_max_attempts: int
    login_code_min_interval_seconds: int
    login_code_max_per_email_per_hour: int
    login_code_max_per_ip_per_hour: int
    session_ttl_days: int
    bgutil_pot_provider_base_url: str
    ffmpeg_path: str
    ffprobe_path: str

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @property
    def session_cookie_name(self) -> str:
        return SESSION_COOKIE_NAME_PRODUCTION if self.is_production else SESSION_COOKIE_NAME

    @property
    def session_cookie_secure(self) -> bool:
        return self.is_production

    @property
    def using_dev_secrets(self) -> bool:
        return self.auth_secret_key == DEV_AUTH_SECRET_KEY or self.ip_hash_secret == DEV_IP_HASH_SECRET


def _load_secrets(is_production: bool) -> tuple[str, str]:
    auth_secret = os.getenv("AUTH_SECRET_KEY", "").strip()
    ip_secret = os.getenv("IP_HASH_SECRET", "").strip()
    if is_production:
        for name, value in (("AUTH_SECRET_KEY", auth_secret), ("IP_HASH_SECRET", ip_secret)):
            if len(value) < MIN_SECRET_LENGTH:
                raise RuntimeError(
                    f"{name} é obrigatório em produção e deve ter pelo menos {MIN_SECRET_LENGTH} caracteres."
                )
        if auth_secret == ip_secret:
            raise RuntimeError("AUTH_SECRET_KEY e IP_HASH_SECRET devem ser segredos DIFERENTES.")
        return auth_secret, ip_secret
    return auth_secret or DEV_AUTH_SECRET_KEY, ip_secret or DEV_IP_HASH_SECRET


def load_settings() -> Settings:
    env = os.getenv("ENV", "development").strip().lower()
    auth_secret, ip_secret = _load_secrets(is_production=(env == "production"))
    return Settings(
        env=env,
        log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper(),
        public_base_url=os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").strip(),
        database_url=normalize_database_url(os.getenv("DATABASE_URL", "").strip() or DEFAULT_DATABASE_URL),
        # Pool pequeno: o ambiente do Render tem pouca memória e limite de conexões.
        db_pool_size=_int_env("DB_POOL_SIZE", default=5, minimum=1),
        db_max_overflow=_int_env("DB_MAX_OVERFLOW", default=2, minimum=0),
        auth_secret_key=auth_secret,
        ip_hash_secret=ip_secret,
        # "console" só é aceito fora de produção. Em produção fica vazio até o provedor ser escolhido.
        email_sender=os.getenv("EMAIL_SENDER", "" if env == "production" else "console").strip().lower(),
        login_code_ttl_seconds=_int_env("AUTH_CODE_TTL_SECONDS", default=600, minimum=1),
        login_code_max_attempts=_int_env("AUTH_CODE_MAX_ATTEMPTS", default=5, minimum=1),
        login_code_min_interval_seconds=_int_env("AUTH_CODE_MIN_INTERVAL_SECONDS", default=60, minimum=0),
        login_code_max_per_email_per_hour=_int_env("AUTH_CODE_MAX_PER_EMAIL_PER_HOUR", default=5, minimum=1),
        login_code_max_per_ip_per_hour=_int_env("AUTH_CODE_MAX_PER_IP_PER_HOUR", default=20, minimum=1),
        session_ttl_days=_int_env("AUTH_SESSION_TTL_DAYS", default=30, minimum=1),
        bgutil_pot_provider_base_url=os.getenv("BGUTIL_POT_PROVIDER_BASE_URL", BGUTIL_POT_PROVIDER_BASE_URL).strip(),
        ffmpeg_path=os.getenv("FFMPEG_PATH", FFMPEG_PATH).strip() or FFMPEG_PATH,
        ffprobe_path=os.getenv("FFPROBE_PATH", FFPROBE_PATH).strip() or FFPROBE_PATH,
    )


settings = load_settings()


def get_settings() -> Settings:
    """Configuração atual. Lida no momento da chamada (permite trocar `settings` nos testes)."""
    return settings
