"""moderação prévia de comentários: comments.approved

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-06

Migration escrita à mão e congelada: NÃO importa nada dos modelos. Não altera 0001-0010.

Só ADD COLUMN em comments, not null, server_default false (pedido do CÉREBRO, 06/10/2026): com o
formulário de comentar liberado de verdade, qualquer plano pago vigente podia postar e o texto já
aparecia no site na hora, sem revisão -- isso deixou de ser aceitável. A partir desta migration,
todo comentário nasce approved=False (fila de espera) e só fica público depois que o CÉREBRO
aprova manualmente. server_default garante que comentários REAIS já publicados antes desta coluna
existir voltam para a fila (approved=False) em vez de ficarem públicos por omissão -- exatamente o
efeito que o CÉREBRO pediu ("já postei comentários, isso tá perigoso"). Os 5 de exemplo
(is_example=True) não são afetados na prática: já estão fora da listagem pública desde a migration
anterior (0010 só mexeu em texto; o filtro de exibição é em services/comments.py), e aqui recebem
approved=True só para não ficarem num estado "pendente" sem sentido (nunca passam por moderação).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("comments", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("approved", sa.Boolean(), nullable=False, server_default=sa.false())
        )

    comments_table = sa.table(
        "comments",
        sa.column("is_example", sa.Boolean()),
        sa.column("approved", sa.Boolean()),
    )
    op.execute(
        comments_table.update().where(comments_table.c.is_example.is_(True)).values(approved=True)
    )


def downgrade() -> None:
    with op.batch_alter_table("comments", schema=None) as batch_op:
        batch_op.drop_column("approved")
