"""Estatísticas PÚBLICAS do produto (Etapa 6 da revisão de UX, aprovação do CÉREBRO): hoje só a
contagem total de gerações COMPLETED, exibida no site como prova social honesta.

Por que isto NÃO é uma regra de negócio (não entra em BUSINESS/USAGE_ALLOWED_CONSUMERS de
tests/test_usage_architecture.py): não decide cota, não decide plano, não decide se uma geração
pode ou não acontecer -- é uma contagem agregada, pública, sem usuário associado, só para exibição.
Por isso este módulo é propositalmente isolado de services/usage.py e companhia (ver
test_stats_arquitetura.py).

Conta apenas status == "completed" (Generation.GENERATION_STATUSES) -- nunca "reserved" (ainda em
andamento, pode nem terminar) nem "failed" (não entregou nada). Esse é exatamente o mesmo conjunto
que o resto do projeto já trata como "geração que o usuário efetivamente recebeu" (ver
services/usage.py e services/result_storage.py).

Explicitamente REJEITADO nesta etapa (pedido do CÉREBRO, 03/10/2026): número aleatório/fake de
"gerações entregues" ou um contador fake de "pessoas online" -- risco de publicidade enganosa
(CDC Art. 37). Este módulo só pode devolver uma contagem REAL, direto da tabela generations.
"""
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database.models import Generation


def get_total_completed_generations(db: Session) -> int:
    """Quantas gerações já foram entregues (status completed), desde sempre. Uma única query
    agregada, sem paginação nem necessidade dela -- COUNT no banco, nunca carrega linhas."""
    total = db.execute(
        select(func.count()).select_from(Generation).where(Generation.status == "completed")
    ).scalar_one()
    return int(total)
