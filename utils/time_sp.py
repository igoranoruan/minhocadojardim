"""Horário de São Paulo. Toda regra de semana/dia/expiração usa este módulo.

`SP` e `now_sp()` já existiam e continuam iguais. Foram acrescentadas as regras comerciais:
- dia de São Paulo (limite diário dos planos pagos);
- semana de São Paulo, começando na SEGUNDA-feira (limite semanal do Free).

As regras comerciais nunca chamam datetime.now() diretamente: usam `now_sp()` (ou recebem `now`).
Os timestamps continuam sendo guardados em UTC pelo banco (database/types.py).
"""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from config import TIMEZONE

SP = ZoneInfo(TIMEZONE)


def now_sp() -> datetime:
    """Agora, com fuso America/Sao_Paulo."""
    return datetime.now(SP)


def to_sp(moment: datetime) -> datetime:
    """Converte um datetime COM fuso para São Paulo. Datetime sem fuso é recusado (seria ambíguo)."""
    if moment.tzinfo is None or moment.tzinfo.utcoffset(moment) is None:
        raise ValueError("datetime sem fuso não é aceito: use datetime com fuso.")
    return moment.astimezone(SP)


def resolve_now(moment: datetime | None = None) -> datetime:
    """`moment` em São Paulo, ou agora (now_sp) se não for informado. Permite injetar o relógio nos testes."""
    return now_sp() if moment is None else to_sp(moment)


def sp_day(moment: datetime | None = None) -> date:
    """Data (dia civil) de São Paulo do instante informado (ou de agora)."""
    return resolve_now(moment).date()


def sp_week_start(moment: datetime | None = None) -> date:
    """Segunda-feira (início da semana) de São Paulo do instante informado (ou de agora)."""
    day = sp_day(moment)
    return day - timedelta(days=day.weekday())  # weekday(): segunda = 0 ... domingo = 6
