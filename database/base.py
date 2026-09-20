"""Base declarativa e convenções de nomes (compatíveis com SQLite e PostgreSQL).

Regra do projeto: toda constraint e todo índice tem nome explícito, escrito igual
nos modelos e na migration. Só as chaves primárias recebem nome automático
(pk_<tabela>), pela convenção abaixo.
"""
from collections.abc import Iterable

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION = {"pk": "pk_%(table_name)s"}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def sql_in(column: str, values: Iterable[str]) -> str:
    """Monta `coluna IN ('a', 'b')` para CHECK de status (texto + CHECK, sem enum nativo)."""
    quoted = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({quoted})"
