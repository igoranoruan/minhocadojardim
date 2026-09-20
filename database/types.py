"""Tipos e utilitários de persistência.

Todo timestamp é guardado em UTC. O SQLite não guarda fuso horário, então o
UTCDateTime converte na entrada e devolve sempre um datetime COM fuso UTC na
saída. Assim o comportamento é o mesmo no SQLite (dev) e no PostgreSQL (produção).

A regra de dia/semana de São Paulo NÃO fica aqui: ela pertence à etapa de uso.
"""
from datetime import datetime, timezone

from sqlalchemy import DateTime
from sqlalchemy.types import TypeDecorator


def utcnow() -> datetime:
    """Agora, em UTC e com fuso. Usado nos defaults dos modelos (gerado no Python, não no banco)."""
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator):
    """DateTime com fuso que sempre entra e sai em UTC."""

    impl = DateTime
    cache_ok = True

    def __init__(self) -> None:
        super().__init__(timezone=True)

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("datetime sem fuso não é aceito: use datetime com fuso (UTC).")
        return value.astimezone(timezone.utc)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
