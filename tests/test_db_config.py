import pytest
from sqlalchemy.exc import OperationalError

from config import DEFAULT_DATABASE_URL, load_settings, normalize_database_url
from database.session import create_db_engine


@pytest.mark.parametrize(
    "entrada, esperado",
    [
        ("postgres://u:p@host:5432/db", "postgresql+psycopg://u:p@host:5432/db"),
        ("postgresql://u:p@host:5432/db", "postgresql+psycopg://u:p@host:5432/db"),
        ("postgresql+psycopg://u:p@host:5432/db", "postgresql+psycopg://u:p@host:5432/db"),
        ("sqlite:///./data/minhoca.db", "sqlite:///./data/minhoca.db"),
    ],
)
def test_normalize_database_url(entrada, esperado):
    assert normalize_database_url(entrada) == esperado


def test_settings_usam_sqlite_local_por_padrao(monkeypatch):
    for nome in ("DATABASE_URL", "DB_POOL_SIZE", "DB_MAX_OVERFLOW"):
        monkeypatch.delenv(nome, raising=False)
    s = load_settings()
    assert s.database_url == DEFAULT_DATABASE_URL == "sqlite:///./data/minhoca.db"
    assert (s.db_pool_size, s.db_max_overflow) == (5, 2)


def test_settings_leem_e_convertem_database_url(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@host:5432/db")
    monkeypatch.setenv("DB_POOL_SIZE", "3")
    monkeypatch.setenv("DB_MAX_OVERFLOW", "0")
    s = load_settings()
    assert s.database_url == "postgresql+psycopg://u:p@host:5432/db"
    assert (s.db_pool_size, s.db_max_overflow) == (3, 0)


def test_settings_rejeitam_pool_invalido(monkeypatch):
    monkeypatch.setenv("DB_POOL_SIZE", "abc")
    with pytest.raises(ValueError):
        load_settings()
    monkeypatch.setenv("DB_POOL_SIZE", "0")
    with pytest.raises(ValueError):
        load_settings()


def test_engine_sqlite_cria_a_pasta_do_banco(tmp_path):
    destino = tmp_path / "subpasta" / "minhoca.db"
    eng = create_db_engine(f"sqlite:///{destino.as_posix()}")
    try:
        assert destino.parent.is_dir()
    finally:
        eng.dispose()


def test_engine_postgres_usa_o_pool_configurado():
    pytest.importorskip("psycopg")
    eng = create_db_engine("postgres://u:p@localhost:5432/db", pool_size=3, max_overflow=1)
    try:
        assert eng.url.drivername == "postgresql+psycopg"
        assert eng.pool.size() == 3  # não conecta: só valida a configuração
    finally:
        eng.dispose()


def test_engine_sqlite_com_pasta_invalida_so_falha_na_conexao(tmp_path):
    arquivo = tmp_path / "arquivo"
    arquivo.write_text("isto é um arquivo, não uma pasta")
    eng = create_db_engine(f"sqlite:///{(arquivo / 'minhoca.db').as_posix()}")  # não levanta ao criar
    try:
        with pytest.raises(OperationalError):
            eng.connect()
    finally:
        eng.dispose()
