import re

import pytest
from sqlalchemy import CheckConstraint, inspect, insert, text
from sqlalchemy.exc import IntegrityError

from config import SQLITE_BUSY_TIMEOUT_MS
from database.base import Base
from database.models import Payment, User

EXPECTED_TABLES = {"users", "payments", "entitlements", "generations", "batches", "payment_events"}


def _norm(sql: str) -> str:
    return re.sub(r"\s+", "", sql).lower()


def test_metadata_dos_modelos_tem_as_tabelas_esperadas():
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_todas_as_tabelas_existem_no_banco_migrado(engine):
    assert EXPECTED_TABLES <= set(inspect(engine).get_table_names())


def test_pragmas_do_sqlite_em_toda_conexao(engine):
    with engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        assert conn.exec_driver_sql("PRAGMA journal_mode").scalar().lower() == "wal"
        assert conn.exec_driver_sql("PRAGMA busy_timeout").scalar() == SQLITE_BUSY_TIMEOUT_MS


def test_foreign_keys_ativas_no_sqlite(session):
    with pytest.raises(IntegrityError):
        session.execute(
            insert(Payment.__table__).values(
                user_id=999_999,
                plan_code="weekly",
                amount_cents=990,
                method="pix",
                status="pending",
                external_reference="ref-fk",
            )
        )
    session.rollback()


def test_apagar_usuario_nao_destroi_historico_financeiro(session, factory):
    payment = factory.payment()
    user = session.get(User, payment.user_id)
    session.delete(user)
    with pytest.raises(IntegrityError):  # RESTRICT: nada de exclusão em cascata
        session.commit()
    session.rollback()
    assert session.execute(text("SELECT COUNT(*) FROM payments")).scalar_one() == 1


def test_indices_esperados_existem(engine):
    inspector = inspect(engine)
    gen = {i["name"] for i in inspector.get_indexes("generations")}
    assert "ix_generations_user_id_period_week_plan_code_status" in gen
    assert "ix_generations_user_id_period_day_plan_code_status" in gen
    assert "ix_payments_user_id_created_at" in {i["name"] for i in inspector.get_indexes("payments")}
    assert "ix_entitlements_user_id_status_expires_at" in {
        i["name"] for i in inspector.get_indexes("entitlements")
    }


def test_checks_dos_modelos_estao_na_migration(engine):
    """O `alembic check` não compara CHECK: este teste evita que modelo e migration divirjam."""
    with engine.connect() as conn:
        rows = conn.exec_driver_sql("SELECT name, sql FROM sqlite_master WHERE type = 'table'").all()
    ddl = {name: _norm(sql or "") for name, sql in rows}

    faltando = []
    for table in Base.metadata.sorted_tables:
        for constraint in table.constraints:
            if isinstance(constraint, CheckConstraint):
                esperado = _norm(f"CONSTRAINT {constraint.name} CHECK ({constraint.sqltext})")
                if esperado not in ddl[table.name]:
                    faltando.append(f"{table.name}.{constraint.name}")
    assert not faltando, f"CHECKs dos modelos ausentes na migration: {faltando}"
