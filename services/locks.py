"""Serialização por usuário (base da reserva de geração e da cadeia de entitlements).

lock_user_row() faz um UPDATE "vazio" (SET updated_at = updated_at) na linha do usuário e deve ser a
PRIMEIRA instrução da transação. Assim, "ler o saldo e gravar a reserva" acontece um de cada vez
por usuário, sem contador extra e sem Redis.

Comportamento por banco (documentado porque depende do banco):
- PostgreSQL: o UPDATE toma um lock de LINHA. Uma segunda transação para o MESMO usuário espera o
  commit/rollback da primeira; usuários diferentes não se bloqueiam. Exige o nível de isolamento
  padrão READ COMMITTED (cada instrução vê o que já foi commitado): depois de obter o lock, a
  contagem enxerga a reserva da transação anterior.
- SQLite (desenvolvimento): o UPDATE toma o lock de ESCRITA do banco todo (mais restritivo, mas
  correto). Quem chega depois espera até SQLITE_BUSY_TIMEOUT_MS (config.py).
Não usamos SELECT ... FOR UPDATE porque o SQLite o ignora silenciosamente (não travaria nada).
"""
from sqlalchemy import update
from sqlalchemy.orm import Session

from database.models import User


class UserNotFoundError(LookupError):
    """Usuário inexistente."""


def lock_user_row(db: Session, user_id: int) -> None:
    table = User.__table__
    result = db.execute(update(table).where(table.c.id == user_id).values(updated_at=table.c.updated_at))
    if result.rowcount != 1:
        raise UserNotFoundError(f"Usuário {user_id} não encontrado.")
