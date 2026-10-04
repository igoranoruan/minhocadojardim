"""Configuração central do Minhoca de Jardim.

Só entra aqui o que já é usado (ambiente, log, URL pública, fuso horário, banco e autenticação).
Limites de planos, arquivos e concorrência entram nas etapas em que forem usados.
Nenhum secret fica neste arquivo: tudo vem de variáveis de ambiente.
"""
import os
from dataclasses import dataclass, field
from urllib.parse import quote

APP_NAME = "KLANGO.MP4"
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

# ------------------------------------------------------------------- identidade anônima (Free sem login)
# Cookie SEPARADO do cookie de sessão (SESSION_COOKIE_NAME): identifica um "usuário-dispositivo"
# (database/models/anonymous_identity.py) para permitir o Free (5 gerações/semana) sem exigir
# login -- ver services/anon_identity.py e routes/deps.py::get_generation_user. Mesmos atributos
# de segurança do cookie de sessão (HttpOnly, SameSite=Lax, Secure em produção, __Host- em
# produção) -- ver Settings.anon_cookie_name/anon_cookie_secure abaixo.
ANON_TOKEN_BYTES = 32  # 256 bits, mesma entropia do token de sessão
ANON_COOKIE_NAME = "minhoca_anon"
ANON_COOKIE_NAME_PRODUCTION = "__Host-minhoca_anon"
# Debounce de last_seen_at (mesmo padrão de SESSION_LAST_USED_UPDATE_INTERVAL_SECONDS).
ANON_LAST_SEEN_UPDATE_INTERVAL_SECONDS = 3600

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

# Pinterest/Instagram também servem pin/post de IMAGEM (sem vídeo) -- suporte adicionado em
# 03/10/2026 (aprovação do CÉREBRO). Limite PRÓPRIO, bem menor que MAX_VIDEO_SIZE_BYTES: uma
# imagem de verdade nunca chega perto de 100 MB -- mas também não faz sentido reaproveitar o teto
# de vídeo para aceitar, sem querer, um arquivo gigante só porque a extensão é de imagem.
MAX_IMAGE_SIZE_BYTES = 25 * 1024 * 1024

# Carrossel do Instagram (vários slides no mesmo post, 03/10/2026 -- aprovação do CÉREBRO): entregue
# como um .zip com todas as imagens. MAX_CAROUSEL_IMAGES protege contra um post com uma quantidade
# anormal de slides (o Instagram permite até 10 na prática, então 10 já cobre qualquer carrossel
# real -- um valor maior só serviria para aceitar algo fora do normal, nunca para um caso real
# legítimo). MAX_IMAGE_ZIP_SIZE_BYTES é o teto do .zip FINAL (depois de todas as imagens baixadas):
# 10 imagens no teto individual (MAX_IMAGE_SIZE_BYTES, 25 MB) deixariam até 250 MB sem este limite
# próprio -- bem acima do que um carrossel de fotos de verdade pesa, então um teto menor e explícito
# evita um .zip anormalmente grande sem impedir nenhum carrossel real.
MAX_CAROUSEL_IMAGES = 10
MAX_IMAGE_ZIP_SIZE_BYTES = 60 * 1024 * 1024
# URL do PO Token Provider (BGUTIL), se o companion estiver rodando no ambiente. Vazio = o
# yt-dlp tenta o YouTube só com o player client mweb, sem PO Token (funciona para parte dos
# vídeos; especificação do produto: PO Token NAO garante todos os vídeos).
BGUTIL_POT_PROVIDER_BASE_URL = ""

# Proxy residencial (Decodo), usado SÓ para YouTube (diagnóstico fechado em 02/10/2026: o
# bloqueio "Sign in to confirm you're not a bot" + HTTP 429 não é de client/PO-Token, é o IP de
# datacenter do Render sendo tratado como suspeito pelo YouTube -- nenhum ajuste de código
# resolve isso, só trocar o IP de saída). Vazio (host/porta ausentes) = yt-dlp usa a rede direta
# do servidor, comportamento ORIGINAL preservado para TikTok/Instagram/Pinterest (que nunca
# passam por aqui -- ver YtDlpDownloader._apply_youtube_proxy) e para o próprio YouTube quando o
# proxy não estiver configurado. Usuário/senha guardados SEPARADOS do host/porta (em vez de uma
# URL única pronta) para nunca exigir que o Igor url-encode caracteres especiais da senha (ex.:
# "=") na mão -- a montagem da URL final com urllib.parse.quote fica em Settings.youtube_proxy_url.
YOUTUBE_PROXY_HOST = ""
YOUTUBE_PROXY_PORT = ""
YOUTUBE_PROXY_USERNAME = ""
YOUTUBE_PROXY_PASSWORD = ""

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

# ----------------------------------------------------------------------------- indicação (Etapa 8)
# Programa de indicação (revisão de UX, aprovação do CÉREBRO, 03/10/2026): quem indica ganha dias
# extras de plano quando a pessoa indicada faz o PRIMEIRO pagamento aprovado (nunca no cadastro,
# que é grátis e fácil de simular -- ver services/referrals.py).
REFERRAL_CODE_LENGTH = 8  # caracteres do código compartilhável (klango.site/?ref=CODIGO)
REFERRAL_REWARD_DAYS = 7  # dias somados ao entitlement do indicador por indicação paga


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
    # Provedor de e-mail de produção (Etapa 11 -- Brevo, único provedor concreto hoje). Só
    # BrevoEmailSender os lê (services/mailer.py, via build_email_sender), nunca os.getenv()
    # diretamente. brevo_api_key não aparece no repr (é um secret); email_from é o remetente que
    # o destinatário vê no e-mail -- não é sensível, mas fica de fora dos defaults de
    # desenvolvimento (igual mp_access_token) porque não existe um valor de dev razoável para isso.
    brevo_api_key: str = field(repr=False)
    email_from: str
    login_code_ttl_seconds: int
    login_code_max_attempts: int
    login_code_min_interval_seconds: int
    login_code_max_per_email_per_hour: int
    login_code_max_per_ip_per_hour: int
    session_ttl_days: int
    anon_cookie_ttl_days: int
    bgutil_pot_provider_base_url: str
    youtube_proxy_host: str
    youtube_proxy_port: str
    # Credenciais do proxy nunca aparecem no repr (secrets, igual mp_access_token).
    youtube_proxy_username: str = field(repr=False)
    youtube_proxy_password: str = field(repr=False)
    ffmpeg_path: str
    ffprobe_path: str
    # Mercado Pago (Etapa 10.2). Nenhuma validação de prefixo (ex.: "TEST-") é feita aqui de
    # propósito -- não é responsabilidade deste projeto adivinhar o formato de uma credencial de
    # terceiro, e uma mudança de formato do lado do Mercado Pago não deveria quebrar o load_settings.
    # mp_access_token nunca aparece no repr (backend-only, nunca vai ao frontend -- só
    # payments/client.py o lê). mp_public_key É PARA aparecer no frontend (routes/payments.py a
    # serve via GET /api/payments/public-key) -- por isso fica de fora do field(repr=False).
    mp_access_token: str = field(repr=False)
    mp_public_key: str
    # Webhook (Etapa 10.3). Backend-only, nunca exposto em rota nenhuma (diferente de
    # mp_public_key, que é intencionalmente pública) -- só payments/webhook_signature.py o lê, via
    # config.get_settings() (chamado por routes/webhooks.py). Obrigatório em produção: diferente
    # de mp_access_token/mp_public_key (que a 10.2 deixou opcionais por escolha mínima), um secret
    # vazio aqui significa "todo webhook recusado por padrão" (ver payments/webhook_signature.py)
    # -- silenciosamente nunca funcionar em produção é pior que falhar alto no startup.
    mp_webhook_secret: str = field(repr=False)

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
    def anon_cookie_name(self) -> str:
        return ANON_COOKIE_NAME_PRODUCTION if self.is_production else ANON_COOKIE_NAME

    @property
    def anon_cookie_secure(self) -> bool:
        return self.is_production

    @property
    def using_dev_secrets(self) -> bool:
        return self.auth_secret_key == DEV_AUTH_SECRET_KEY or self.ip_hash_secret == DEV_IP_HASH_SECRET

    @property
    def youtube_proxy_url(self) -> str:
        """Monta a URL final do proxy (http://usuario:senha@host:porta) só quando host E porta
        estão configurados -- usuário/senha podem ficar vazios (alguns provedores não exigem).
        quote() (safe="") escapa qualquer caractere especial da senha (ex.: "=", "@", ":") para
        nunca quebrar a URL nem ser interpretado como separador."""
        if not self.youtube_proxy_host or not self.youtube_proxy_port:
            return ""
        user = quote(self.youtube_proxy_username, safe="")
        password = quote(self.youtube_proxy_password, safe="")
        auth = f"{user}:{password}@" if user or password else ""
        return f"http://{auth}{self.youtube_proxy_host}:{self.youtube_proxy_port}"


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


def _load_webhook_secret(is_production: bool) -> str:
    secret = os.getenv("MP_WEBHOOK_SECRET", "").strip()
    if is_production and not secret:
        raise RuntimeError("MP_WEBHOOK_SECRET é obrigatório em produção.")
    return secret


def _load_email_provider_secrets(is_production: bool) -> tuple[str, str]:
    """BREVO_API_KEY/EMAIL_FROM -- mesmo padrão de MP_WEBHOOK_SECRET: obrigatórios em produção
    (silenciosamente nunca enviar e-mail em produção é pior que falhar alto no startup). Em
    desenvolvimento (ConsoleEmailSender) não fazem falta, então ficam vazios se ausentes."""
    api_key = os.getenv("BREVO_API_KEY", "").strip()
    email_from = os.getenv("EMAIL_FROM", "").strip()
    if is_production:
        if not api_key:
            raise RuntimeError("BREVO_API_KEY é obrigatório em produção.")
        if not email_from:
            raise RuntimeError("EMAIL_FROM é obrigatório em produção.")
    return api_key, email_from


def load_settings() -> Settings:
    env = os.getenv("ENV", "development").strip().lower()
    auth_secret, ip_secret = _load_secrets(is_production=(env == "production"))
    mp_webhook_secret = _load_webhook_secret(is_production=(env == "production"))
    brevo_api_key, email_from = _load_email_provider_secrets(is_production=(env == "production"))
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
        brevo_api_key=brevo_api_key,
        email_from=email_from,
        login_code_ttl_seconds=_int_env("AUTH_CODE_TTL_SECONDS", default=600, minimum=1),
        login_code_max_attempts=_int_env("AUTH_CODE_MAX_ATTEMPTS", default=5, minimum=1),
        login_code_min_interval_seconds=_int_env("AUTH_CODE_MIN_INTERVAL_SECONDS", default=60, minimum=0),
        login_code_max_per_email_per_hour=_int_env("AUTH_CODE_MAX_PER_EMAIL_PER_HOUR", default=5, minimum=1),
        login_code_max_per_ip_per_hour=_int_env("AUTH_CODE_MAX_PER_IP_PER_HOUR", default=20, minimum=1),
        session_ttl_days=_int_env("AUTH_SESSION_TTL_DAYS", default=30, minimum=1),
        # 365 dias: cookie de longa duração (identidade de dispositivo, não uma sessão de login) --
        # o navegador pode limitar isso na prática (ex.: teto de ~400 dias do Chrome), o que é
        # aceitável: o pior caso é o visitante virar "novo dispositivo" mais cedo, nunca um erro.
        anon_cookie_ttl_days=_int_env("ANON_COOKIE_TTL_DAYS", default=365, minimum=1),
        bgutil_pot_provider_base_url=os.getenv("BGUTIL_POT_PROVIDER_BASE_URL", BGUTIL_POT_PROVIDER_BASE_URL).strip(),
        youtube_proxy_host=os.getenv("YOUTUBE_PROXY_HOST", YOUTUBE_PROXY_HOST).strip(),
        youtube_proxy_port=os.getenv("YOUTUBE_PROXY_PORT", YOUTUBE_PROXY_PORT).strip(),
        youtube_proxy_username=os.getenv("YOUTUBE_PROXY_USERNAME", YOUTUBE_PROXY_USERNAME).strip(),
        youtube_proxy_password=os.getenv("YOUTUBE_PROXY_PASSWORD", YOUTUBE_PROXY_PASSWORD),
        ffmpeg_path=os.getenv("FFMPEG_PATH", FFMPEG_PATH).strip() or FFMPEG_PATH,
        ffprobe_path=os.getenv("FFPROBE_PATH", FFPROBE_PATH).strip() or FFPROBE_PATH,
        # Etapa 10.2: sem valor padrão de desenvolvimento (ao contrário de AUTH_SECRET_KEY) --
        # uma chamada real ao Mercado Pago sem token configurado falha na hora da chamada
        # (payments/client.py), nunca no startup; nenhuma validação de formato é feita aqui.
        mp_access_token=os.getenv("MP_ACCESS_TOKEN", "").strip(),
        mp_public_key=os.getenv("MP_PUBLIC_KEY", "").strip(),
        mp_webhook_secret=mp_webhook_secret,
    )


settings = load_settings()


def get_settings() -> Settings:
    """Configuração atual. Lida no momento da chamada (permite trocar `settings` nos testes)."""
    return settings
