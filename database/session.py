"""Engine e sessão do banco.

- Uma sessão por requisição, via dependência do FastAPI (get_session).
- Commit e rollback são EXPLÍCITOS: quem grava dados (os services) chama
  session.commit(). A dependência só faz rollback em caso de erro e fecha a sessão.
- SQLite (dev): foreign_keys ligado, WAL e busy_timeout em toda conexão.
- PostgreSQL (produção): pool pequeno e configurável, com pool_pre_ping.
- Engine criado sob demanda (não na importação), para facilitar testes.
"""
import logging
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from config import SQLITE_BUSY_TIMEOUT_MS, normalize_database_url, settings

logger = logging.getLogger("minhoca")


def _configure_sqlite_connection(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute(f"PRAGMA busy_timeout={int(SQLITE_BUSY_TIMEOUT_MS)}")
    finally:
        cursor.close()


def create_db_engine(url: str, *, pool_size: int = 5, max_overflow: int = 2) -> Engine:
    """Cria o engine para SQLite ou PostgreSQL a partir da DATABASE_URL."""
    url_obj = make_url(normalize_database_url(url))
    is_sqlite = url_obj.get_backend_name() == "sqlite"

    kwargs: dict = {"pool_pre_ping": True}
    if is_sqlite:
        database = url_obj.database
        if database and database != ":memory:":
            try:
                Path(database).parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                # A falha real aparece na primeira conexão (e o /health/ready responde 503).
                logger.warning("[DB] não foi possível criar a pasta do banco %r: %s", database, exc)
        kwargs["connect_args"] = {
            "check_same_thread": False,
            "timeout": SQLITE_BUSY_TIMEOUT_MS / 1000,
        }
    else:
        kwargs["pool_size"] = pool_size
        kwargs["max_overflow"] = max_overflow

    engine = create_engine(url_obj, **kwargs)
    if is_sqlite:
        event.listen(engine, "connect", _configure_sqlite_connection)
    return engine


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    return create_db_engine(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
    )


@lru_cache(maxsize=1)
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """Dependência do FastAPI: uma sessão por requisição."""
    session = get_sessionmaker()()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def dispose_engine() -> None:
    """Fecha o pool de conexões (chamado no shutdown da aplicação)."""
    if get_engine.cache_info().currsize:
        get_engine().dispose()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()
