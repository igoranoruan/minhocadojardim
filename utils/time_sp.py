"""Horário de São Paulo. Toda regra de semana/dia/expiração usa este módulo."""
from datetime import datetime
from zoneinfo import ZoneInfo

from config import TIMEZONE

SP = ZoneInfo(TIMEZONE)


def now_sp() -> datetime:
    """Agora, com fuso America/Sao_Paulo."""
    return datetime.now(SP)
