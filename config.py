"""Configuração central do Minhoca de Jardim.

Etapa 1: só o que já é usado (ambiente, log, URL pública, fuso horário).
Limites de planos, arquivos e concorrência entram nas etapas em que forem usados.
Nenhum secret fica neste arquivo: tudo vem de variáveis de ambiente.
"""
import os
from dataclasses import dataclass

APP_NAME = "Minhoca de Jardim"
APP_VERSION = "2.0.0-etapa1"

# Todo controle de semana/dia (limites Free, Semanal etc.) usa este fuso.
TIMEZONE = "America/Sao_Paulo"


@dataclass(frozen=True)
class Settings:
    env: str
    log_level: str
    public_base_url: str

    @property
    def is_production(self) -> bool:
        return self.env == "production"


def load_settings() -> Settings:
    return Settings(
        env=os.getenv("ENV", "development").strip().lower(),
        log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper(),
        public_base_url=os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").strip(),
    )


settings = load_settings()
