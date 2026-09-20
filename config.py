"""Configuração central do Minhoca de Jardim.

Só entra aqui o que já é usado (ambiente, log, URL pública, fuso horário e banco).
Limites de planos, arquivos e concorrência entram nas etapas em que forem usados.
Nenhum secret fica neste arquivo: tudo vem de variáveis de ambiente.
"""
import os
from dataclasses import dataclass

APP_NAME = "Minhoca de Jardim"
APP_VERSION = "2.0.0-etapa1"

# Todo controle de semana/dia (limites Free, Semanal etc.) usa este fuso.
TIMEZONE = "America/Sao_Paulo"

# Banco de desenvolvimento (SQLite local, pasta ignorada pelo Git).
DEFAULT_DATABASE_URL = "sqlite:///./data/minhoca.db"

# Tempo máximo (ms) que o SQLite espera por um lock de escrita antes de dar erro.
SQLITE_BUSY_TIMEOUT_MS = 15000


def normalize_database_url(url: str) -> str:
    """Faz o Render/Heroku-style URL funcionar com o driver psycopg (v3).

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

    @property
    def is_production(self) -> bool:
        return self.env == "production"


def load_settings() -> Settings:
    return Settings(
        env=os.getenv("ENV", "development").strip().lower(),
        log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper(),
        public_base_url=os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").strip(),
        database_url=normalize_database_url(os.getenv("DATABASE_URL", "").strip() or DEFAULT_DATABASE_URL),
        # Pool pequeno: o ambiente do Render tem pouca memória e limite de conexões.
        db_pool_size=_int_env("DB_POOL_SIZE", default=5, minimum=1),
        db_max_overflow=_int_env("DB_MAX_OVERFLOW", default=2, minimum=0),
    )


settings = load_settings()
