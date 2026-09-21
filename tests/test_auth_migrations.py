from alembic import command
from sqlalchemy import inspect, text

from database.session import create_db_engine

AUTH_TABLES = {"login_codes", "auth_sessions"}
ETAPA2_TABLES = {"users", "payments", "entitlements", "generations", "batches", "payment_events"}
USERS_COLUNAS = ["id", "email", "email_verified_at", "created_at", "updated_at"]


def _abrir(db_url):
    return create_db_engine(db_url)


def _tabelas(db_url):
    eng = _abrir(db_url)
    try:
        return set(inspect(eng).get_table_names()) - {"alembic_version"}
    finally:
        eng.dispose()


def _colunas_users(db_url):
    eng = _abrir(db_url)
    try:
        with eng.connect() as conn:
            return [linha[1] for linha in conn.exec_driver_sql("PRAGMA table_info(users)").all()]
    finally:
        eng.dispose()


def test_0002_upgrade_cria_as_tabelas_de_autenticacao(db_url, alembic_cfg):
    command.upgrade(alembic_cfg, "0001")
    assert _tabelas(db_url) == ETAPA2_TABLES

    command.upgrade(alembic_cfg, "0002")
    assert _tabelas(db_url) == ETAPA2_TABLES | AUTH_TABLES


def test_0002_downgrade_remove_so_as_tabelas_da_etapa_3(db_url, alembic_cfg):
    command.upgrade(alembic_cfg, "head")
    command.downgrade(alembic_cfg, "-1")
    assert _tabelas(db_url) == ETAPA2_TABLES  # a Etapa 2 continua intacta

    eng = _abrir(db_url)
    try:
        with eng.connect() as conn:
            assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0001"
    finally:
        eng.dispose()


def test_0002_nao_altera_a_tabela_users(db_url, alembic_cfg):
    command.upgrade(alembic_cfg, "0001")
    antes = _colunas_users(db_url)
    command.upgrade(alembic_cfg, "0002")
    assert _colunas_users(db_url) == antes == USERS_COLUNAS


def test_0002_alembic_check_sem_diferencas(db_url, alembic_cfg):
    command.upgrade(alembic_cfg, "head")
    command.check(alembic_cfg)


def test_0002_downgrade_e_upgrade_de_novo(db_url, alembic_cfg):
    command.upgrade(alembic_cfg, "head")
    command.downgrade(alembic_cfg, "-1")
    command.upgrade(alembic_cfg, "head")
    assert AUTH_TABLES <= _tabelas(db_url)


def test_0002_cria_a_unique_e_os_indices_esperados(engine):
    inspetor = inspect(engine)
    uniques = {u["name"] for u in inspetor.get_unique_constraints("login_codes")}
    assert "uq_login_codes_email_active_slot" in uniques
    assert "uq_auth_sessions_token_hash" in {u["name"] for u in inspetor.get_unique_constraints("auth_sessions")}
    indices = {i["name"] for t in ("login_codes", "auth_sessions") for i in inspetor.get_indexes(t)}
    assert {"ix_login_codes_email_created_at", "ix_login_codes_ip_hash_created_at", "ix_auth_sessions_user_id"} <= indices


def test_a_fk_de_sessao_para_usuario_e_restrict(engine):
    fks = inspect(engine).get_foreign_keys("auth_sessions")
    assert len(fks) == 1
    assert fks[0]["referred_table"] == "users"
    assert fks[0]["options"].get("ondelete", "").upper() == "RESTRICT"
