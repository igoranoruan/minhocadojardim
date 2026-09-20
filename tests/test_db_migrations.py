from alembic import command
from sqlalchemy import inspect, text

from database.session import create_db_engine

APP_TABLES = {"users", "payments", "entitlements", "generations", "batches", "payment_events"}


def _tables(db_url: str) -> set[str]:
    eng = create_db_engine(db_url)
    try:
        return set(inspect(eng).get_table_names())
    finally:
        eng.dispose()


def test_alembic_upgrade_cria_todas_as_tabelas(db_url, alembic_cfg):
    command.upgrade(alembic_cfg, "head")
    tables = _tables(db_url)
    assert APP_TABLES <= tables
    assert "alembic_version" in tables

    eng = create_db_engine(db_url)
    try:
        with eng.connect() as conn:
            assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0001"
    finally:
        eng.dispose()


def test_alembic_downgrade_remove_as_tabelas(db_url, alembic_cfg):
    command.upgrade(alembic_cfg, "head")
    command.downgrade(alembic_cfg, "-1")
    assert _tables(db_url) - {"alembic_version"} == set()


def test_alembic_upgrade_downgrade_upgrade(db_url, alembic_cfg):
    command.upgrade(alembic_cfg, "head")
    command.downgrade(alembic_cfg, "-1")
    command.upgrade(alembic_cfg, "head")
    assert APP_TABLES <= _tables(db_url)


def test_alembic_check_sem_diferencas_entre_modelos_e_banco(db_url, alembic_cfg):
    command.upgrade(alembic_cfg, "head")
    command.check(alembic_cfg)  # levanta exceção se os modelos divergirem das migrations
