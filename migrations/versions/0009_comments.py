"""área de comentários públicos: tabela comments + 5 comentários de exemplo

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-05

Migration escrita à mão e congelada: NÃO importa nada dos modelos. Não altera 0001-0008.

Tabela nova `comments` -- ver database/models/comment.py para o contrato completo. Resumo:
leitura pública (sem login), escrita só para quem tem plano pago VIGENTE no instante do post
(services/comments.py), exclusão só do admin (routes/deps.py::require_admin).

Esta migration também insere os 5 comentários DE EXEMPLO (is_example=true, user_id=NULL) pedidos
pelo CÉREBRO para a seção não nascer vazia -- nunca apresentados como depoimento real (o frontend
rotula "Exemplo" sempre que is_example é true). São o único dado de exemplo inserido por uma
migration neste projeto; nenhuma outra tabela usa este padrão.
"""
from datetime import datetime, timedelta, timezone
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "comments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("author_name", sa.String(length=80), nullable=False),
        sa.Column("body", sa.String(length=500), nullable=False),
        sa.Column("is_example", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_comments"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_comments_user_id_users", ondelete="RESTRICT"),
        sa.CheckConstraint("length(trim(author_name)) >= 1", name="ck_comments_author_name_nao_vazio"),
        sa.CheckConstraint("length(trim(body)) >= 1", name="ck_comments_body_nao_vazio"),
    )
    op.create_index("ix_comments_created_at", "comments", ["created_at"])

    comments_table = sa.table(
        "comments",
        sa.column("user_id", sa.Integer()),
        sa.column("author_name", sa.String()),
        sa.column("body", sa.String()),
        sa.column("is_example", sa.Boolean()),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    agora = datetime.now(timezone.utc)
    exemplos = [
        ("Rafael M.", "Uso pra separar os melhores lances de futebol pro meu perfil, economizo um tempão com o processamento em lote.", 5),
        ("Camila S.", "Baixo os vídeos, boto minha narração por cima e pronto: conteúdo novo todo dia sem precisar aparecer.", 4),
        ("Diego A.", "Simples e rápido, resolveu o que eu precisava pra reaproveitar clipes de jogos sem complicação.", 3),
        ("Juliana P.", "Uso pra montar compilados de um nicho só. Bem prático pra quem edita bastante vídeo curto.", 2),
        ("Bruno T.", "Já virou parte da minha rotina de trabalho, facilita muito baixar vários vídeos de uma vez.", 1),
    ]
    op.bulk_insert(
        comments_table,
        [
            {
                "user_id": None,
                "author_name": nome,
                "body": texto,
                "is_example": True,
                "created_at": agora - timedelta(days=dias_atras),
            }
            for nome, texto, dias_atras in exemplos
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_comments_created_at", table_name="comments")
    op.drop_table("comments")
