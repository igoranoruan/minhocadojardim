"""atualiza o texto dos 5 comentários de exemplo (revisão de copy, aprovação do CÉREBRO)

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-06

Migration escrita à mão e congelada: NÃO importa nada dos modelos. Não altera 0001-0009.

Só UPDATE nas 5 linhas de exemplo (is_example=true) que a 0009 já inseriu -- nunca uma nova
INSERT nem mudança de schema. Motivo (pedido do CÉREBRO, 06/10/2026): os nomes completos
("Rafael M.", "Camila S." etc.) e as frases longas/uniformes ("Uso para X porque Y...") tinham
cara de depoimento forjado, o oposto do efeito pretendido. Trocados por apelidos/handles mais
parecidos com o que um usuário de verdade usaria, e textos curtos, informais e de tamanho
variado -- nunca a mesma fórmula duas vezes. Continuam com is_example=true (selo "Exemplo" no
site não muda); 0009 nunca é editada porque já pode ter rodado em produção.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (nome antigo, nome novo, texto novo) -- o texto antigo não importa pra fazer o UPDATE (ver
# upgrade()), só serve pra downgrade() devolver exatamente o estado anterior.
_TROCAS = [
    ("Rafael M.", "igod22", "salvou muito tempo aqui"),
    ("Camila S.", "Nanda", "baixei e já consegui usar no editor sem ficar mexendo no arquivo"),
    ("Diego A.", "Neto", "bem mais prático pra baixar vários"),
    ("Juliana P.", "Topre", "testei o lote e foi bem rápido"),
    ("Bruno T.", "duda_", "mt bom, uso toda semana"),
]

_TEXTOS_ANTIGOS = {
    "Rafael M.": "Uso pra separar os melhores lances de futebol pro meu perfil, economizo um tempão com o processamento em lote.",
    "Camila S.": "Baixo os vídeos, boto minha narração por cima e pronto: conteúdo novo todo dia sem precisar aparecer.",
    "Diego A.": "Simples e rápido, resolveu o que eu precisava pra reaproveitar clipes de jogos sem complicação.",
    "Juliana P.": "Uso pra montar compilados de um nicho só. Bem prático pra quem edita bastante vídeo curto.",
    "Bruno T.": "Já virou parte da minha rotina de trabalho, facilita muito baixar vários vídeos de uma vez.",
}


def upgrade() -> None:
    comments_table = sa.table(
        "comments",
        sa.column("author_name", sa.String()),
        sa.column("body", sa.String()),
        sa.column("is_example", sa.Boolean()),
    )
    for nome_antigo, nome_novo, texto_novo in _TROCAS:
        op.execute(
            comments_table.update()
            .where(comments_table.c.is_example.is_(True))
            .where(comments_table.c.author_name == nome_antigo)
            .values(author_name=nome_novo, body=texto_novo)
        )


def downgrade() -> None:
    comments_table = sa.table(
        "comments",
        sa.column("author_name", sa.String()),
        sa.column("body", sa.String()),
        sa.column("is_example", sa.Boolean()),
    )
    for nome_antigo, nome_novo, _texto_novo in _TROCAS:
        op.execute(
            comments_table.update()
            .where(comments_table.c.is_example.is_(True))
            .where(comments_table.c.author_name == nome_novo)
            .values(author_name=nome_antigo, body=_TEXTOS_ANTIGOS[nome_antigo])
        )
