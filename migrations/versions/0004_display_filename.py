"""nome de exibição opcional da geração: generations.display_filename

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-27

Migration escrita à mão e congelada: NÃO importa nada dos modelos. Não altera 0001/0002/0003.
Só ADD COLUMN em generations, nullable (Etapa 9.3, correção de filename de lote). Não mexe em
output_sha256/output_storage_key/output_size_bytes/output_expires_at nem em nenhuma outra coluna
já existente -- display_filename é só o nome APRESENTADO/entregue ao usuário (Content-Disposition
do download individual e nome de entrada no ZIP), nunca o identificador técnico da geração nem a
chave de armazenamento. Sem índice (não é usada em nenhum filtro/consulta, só lida por id).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("generations", schema=None) as batch_op:
        batch_op.add_column(sa.Column("display_filename", sa.String(length=255), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("generations", schema=None) as batch_op:
        batch_op.drop_column("display_filename")