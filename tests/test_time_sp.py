"""Regras de tempo de São Paulo: dia civil e semana (segunda a domingo)."""
from datetime import date, datetime, timedelta, timezone

import pytest

from utils.time_sp import SP, now_sp, resolve_now, sp_day, sp_week_start, to_sp

UTC = timezone.utc


def sp(y, m, d, h=0, mi=0, s=0):
    return datetime(y, m, d, h, mi, s, tzinfo=SP)


def test_now_sp_continua_igual_e_devolve_horario_de_sao_paulo():
    agora = now_sp()
    assert agora.tzinfo is SP
    assert agora.utcoffset() == timedelta(hours=-3)  # o Brasil não tem horário de verão desde 2019


def test_datetime_sem_fuso_e_recusado():
    with pytest.raises(ValueError):
        to_sp(datetime(2026, 9, 23, 12, 0))
    with pytest.raises(ValueError):
        sp_day(datetime(2026, 9, 23, 12, 0))
    with pytest.raises(ValueError):
        sp_week_start(datetime(2026, 9, 23, 12, 0))


def test_resolve_now_usa_o_relogio_injetado_ou_agora():
    fixo = datetime(2026, 9, 23, 15, 0, tzinfo=UTC)
    assert resolve_now(fixo) == fixo and resolve_now(fixo).tzinfo is SP
    assert resolve_now().tzinfo is SP
    assert isinstance(sp_day(), date) and isinstance(sp_week_start(), date)


# ------------------------------------------------------------------ dia de São Paulo
def test_mudanca_de_dia_e_a_meia_noite_de_sao_paulo():
    assert sp_day(sp(2026, 9, 23, 23, 59, 59)) == date(2026, 9, 23)
    assert sp_day(sp(2026, 9, 24, 0, 0, 0)) == date(2026, 9, 24)


def test_dia_de_sao_paulo_nao_e_o_dia_utc():
    # 22:00 em São Paulo já é o dia seguinte em UTC, mas o dia comercial continua sendo o de SP
    assert sp_day(datetime(2026, 9, 24, 1, 0, tzinfo=UTC)) == date(2026, 9, 23)
    assert sp_day(datetime(2026, 9, 24, 2, 59, 59, tzinfo=UTC)) == date(2026, 9, 23)
    assert sp_day(datetime(2026, 9, 24, 3, 0, 0, tzinfo=UTC)) == date(2026, 9, 24)


# ------------------------------------------------------------------ semana de São Paulo (segunda = início)
@pytest.mark.parametrize(
    "dia",
    [21, 22, 23, 24, 25, 26, 27],
    ids=["segunda", "terca", "quarta", "quinta", "sexta", "sabado", "domingo"],
)
def test_segunda_a_domingo_pertencem_a_mesma_semana(dia):
    assert sp_week_start(sp(2026, 9, dia, 12)) == date(2026, 9, 21)


def test_sexta_sabado_e_domingo_ficam_na_semana_da_segunda_anterior():
    for dia in (25, 26, 27):
        assert sp_week_start(sp(2026, 9, dia, 12)) == date(2026, 9, 21)


def test_segunda_meia_noite_inicia_nova_semana():
    assert sp_week_start(sp(2026, 9, 27, 23, 59, 59)) == date(2026, 9, 21)  # domingo, último segundo
    assert sp_week_start(sp(2026, 9, 28, 0, 0, 0)) == date(2026, 9, 28)  # segunda, primeiro segundo


def test_semana_e_de_sao_paulo_e_nao_de_utc():
    # domingo 23:30 em SP já é segunda 02:30 em UTC: continua na semana ANTERIOR
    assert sp_week_start(datetime(2026, 9, 28, 2, 30, tzinfo=UTC)) == date(2026, 9, 21)
    assert sp_week_start(datetime(2026, 9, 28, 2, 59, 59, tzinfo=UTC)) == date(2026, 9, 21)
    assert sp_week_start(datetime(2026, 9, 28, 3, 0, 0, tzinfo=UTC)) == date(2026, 9, 28)


def test_a_segunda_calculada_e_sempre_segunda():
    for delta in range(0, 40):
        inicio = sp_week_start(sp(2026, 9, 1, 12) + timedelta(days=delta))
        assert inicio.weekday() == 0
