"""Ambiente do Alembic do Minhoca de Jardim.

A URL vem, nesta ordem, de: `sqlalchemy.url` no alembic.ini/Config (usado nos testes)
ou da DATABASE_URL (config.py). O engine é criado pelo mesmo código da aplicação,
então os PRAGMAs do SQLite (foreign_keys, WAL, busy_timeout) valem nas migrations.
"""
from logging.config import fileConfig

from alembic import context

import database.models  # noqa: F401  (registra os modelos no metadata)
from config import settings
from database.base import Base
from database.session import create_db_engine

alembic_config = context.config

if alembic_config.config_file_name is not None and alembic_config.attributes.get("configure_logger", True):
    fileConfig(alembic_config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def get_url() -> str:
    return alembic_config.get_main_option("sqlalchemy.url") or settings.database_url


def run_migrations_offline() -> None:
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_db_engine(
        get_url(),
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
    )
    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                render_as_batch=True,  # necessário para ALTER TABLE no SQLite
                compare_type=True,
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
