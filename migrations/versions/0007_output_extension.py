"""extensão real do resultado entregável: generations.output_extension

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-03

Migration escrita à mão e congelada: NÃO importa nada dos modelos. Não altera 0001/0002/0003/0004/
0005/0006. Só ADD COLUMN em generations, nullable (suporte a imagem -- Pinterest/Instagram também
servem pin/post sem vídeo, aprovação do CÉREBRO). NULL para toda geração já existente antes desta
coluna (todas são vídeo; services/usage.py::complete_generation() trata NULL como "mp4", mesmo
comportamento de sempre). Não mexe em nenhuma outra coluna já existente. Sem índice (não é usada em
nenhum filtro/consulta, só lida por id, igual display_filename em 0004).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("generations", schema=None) as batch_op:
        batch_op.add_column(sa.Column("output_extension", sa.String(length=8), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("generations", schema=None) as batch_op:
        batch_op.drop_column("output_extension")
