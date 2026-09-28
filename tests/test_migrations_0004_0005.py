"""Migrations 0004 (Generation.display_filename) e 0005 (Batch.request_fingerprint) -- Etapa 9.3,
correção. Mesmo padrão de tests/test_generation_output_fields.py::test_0003_downgrade_....
"""
from alembic import command
from sqlalchemy import inspect

from database.session import create_db_engine


def _colunas(db_url, tabela):
    eng = create_db_engine(db_url)
    try:
        return {c["name"] for c in inspect(eng).get_columns(tabela)}
    finally:
        eng.dispose()


def test_0004_e_aditiva_generations_ganha_so_display_filename(db_url, alembic_cfg):
    command.upgrade(alembic_cfg, "0003")
    antes = _colunas(db_url, "generations")
    assert "display_filename" not in antes

    command.upgrade(alembic_cfg, "0004")
    depois = _colunas(db_url, "generations")
    assert depois == antes | {"display_filename"}

    command.downgrade(alembic_cfg, "0003")
    assert _colunas(db_url, "generations") == antes


def test_0005_e_aditiva_batches_ganha_so_request_fingerprint(db_url, alembic_cfg):
    command.upgrade(alembic_cfg, "0004")
    antes = _colunas(db_url, "batches")
    assert "request_fingerprint" not in antes

    command.upgrade(alembic_cfg, "0005")
    depois = _colunas(db_url, "batches")
    assert depois == antes | {"request_fingerprint"}

    command.downgrade(alembic_cfg, "0004")
    assert _colunas(db_url, "batches") == antes


def test_cadeia_completa_0001_ate_0005_sem_erro(db_url, alembic_cfg):
    for rev in ("0001", "0002", "0003", "0004", "0005"):
        command.upgrade(alembic_cfg, rev)  # não levanta = compatíveis entre si, passo a passo


def test_alembic_check_no_head_com_0004_e_0005(db_url, alembic_cfg):
    command.upgrade(alembic_cfg, "head")
    command.check(alembic_cfg)  # levanta se o modelo (display_filename/request_fingerprint) divergir


def test_display_filename_e_nullable_e_sem_indice(engine):
    colunas = {c["name"]: c for c in inspect(engine).get_columns("generations")}
    assert colunas["display_filename"]["nullable"] is True
    indices = {ix["name"] for ix in inspect(engine).get_indexes("generations")}
    assert not any("display_filename" in ix for ix in indices)


def test_request_fingerprint_e_nullable_e_sem_indice(engine):
    colunas = {c["name"]: c for c in inspect(engine).get_columns("batches")}
    assert colunas["request_fingerprint"]["nullable"] is True
    indices = {ix["name"] for ix in inspect(engine).get_indexes("batches")}
    assert not any("request_fingerprint" in ix for ix in indices)