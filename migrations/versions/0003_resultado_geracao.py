"""resultado da geração: output_size_bytes, output_expires_at, output_storage_key

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-23

Migration escrita à mão e congelada: NÃO importa nada dos modelos.
Não altera 0001 nem 0002. Só ADD COLUMN em generations, todos os campos nullable (as gerações já
existentes continuam válidas, sem nenhum resultado disponível). Nenhuma tabela é recriada do zero
nem tem dado apagado.

Usa batch_alter_table (já é o padrão do projeto para SQLite — ver migrations/env.py,
render_as_batch=True): o SQLite não suporta ADD CONSTRAINT depois da tabela já criada, então
adicionar a coluna e o CHECK juntos precisa do modo batch para funcionar nos dois bancos
(no PostgreSQL o batch mode simplesmente emite os comandos ALTER TABLE normais).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("generations", schema=None) as batch_op:
        batch_op.add_column(sa.Column("output_size_bytes", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("output_expires_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("output_storage_key", sa.String(length=255), nullable=True))
        batch_op.create_check_constraint(
            "ck_generations_output_size_bytes_nao_negativo",
            "output_size_bytes IS NULL OR output_size_bytes >= 0",
        )


def downgrade() -> None:
    with op.batch_alter_table("generations", schema=None) as batch_op:
        batch_op.drop_constraint("ck_generations_output_size_bytes_nao_negativo", type_="check")
        batch_op.drop_column("output_storage_key")
        batch_op.drop_column("output_expires_at")
        batch_op.drop_column("output_size_bytes")
