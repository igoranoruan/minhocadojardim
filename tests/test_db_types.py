import time
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import StatementError

from database.types import utcnow


def test_utcnow_tem_fuso_utc():
    agora = utcnow()
    assert agora.tzinfo is not None
    assert agora.utcoffset() == timedelta(0)


def test_timestamps_saem_do_banco_com_fuso_utc(factory, session):
    user = factory.user()
    session.refresh(user)
    assert user.created_at.tzinfo is not None
    assert user.created_at.utcoffset() == timedelta(0)
    assert user.updated_at.utcoffset() == timedelta(0)


def test_datetime_com_outro_fuso_e_convertido_para_utc(factory, session):
    sao_paulo = timezone(timedelta(hours=-3))
    user = factory.user()
    user.email_verified_at = datetime(2026, 9, 20, 12, 0, tzinfo=sao_paulo)
    session.commit()
    session.refresh(user)

    assert user.email_verified_at == datetime(2026, 9, 20, 15, 0, tzinfo=timezone.utc)
    assert user.email_verified_at.utcoffset() == timedelta(0)
    bruto = session.execute(
        text("SELECT email_verified_at FROM users WHERE id = :i"), {"i": user.id}
    ).scalar_one()
    assert str(bruto).startswith("2026-09-20 15:00:00")  # guardado em UTC


def test_datetime_sem_fuso_e_rejeitado(factory, session):
    user = factory.user()
    user.email_verified_at = datetime(2026, 9, 20, 12, 0)  # naive
    with pytest.raises((ValueError, StatementError)):
        session.commit()
    session.rollback()


def test_updated_at_muda_na_atualizacao(factory, session):
    user = factory.user()
    session.refresh(user)
    antes = user.updated_at
    time.sleep(0.01)
    user.email_verified_at = utcnow()
    session.commit()
    session.refresh(user)
    assert user.updated_at > antes
    assert user.created_at <= antes
