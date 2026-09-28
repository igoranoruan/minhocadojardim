"""fingerprint determinístico do conteúdo do lote: batches.request_fingerprint

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-27

Migration escrita à mão e congelada: NÃO importa nada dos modelos. Não altera 0001/0002/0003/0004.
Só ADD COLUMN em batches (Etapa 9.3, correção de idempotência real: detectar mesmo request_id +
mesma quantidade + conteúdo diferente). NULLABLE: lotes já existentes (criados antes desta coluna
existir) não têm como ter um fingerprint retroativo -- não recalculamos nada aqui, e a comparação
de conteúdo (services/usage.py::_batch_replay) já trata request_fingerprint=NULL como "nada a
comparar", nunca como conflito. Todo lote novo (reserve_batch, a partir desta etapa) sempre grava
um fingerprint -- a coluna é opcional só por compatibilidade com dados antigos, não por escolha de
modelagem. Sem índice (nunca é usada em nenhum filtro/consulta, só comparada linha a linha do
próprio Batch já carregado por request_id).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("batches", schema=None) as batch_op:
        batch_op.add_column(sa.Column("request_fingerprint", sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("batches", schema=None) as batch_op:
        batch_op.drop_column("request_fingerprint")