"""Estatísticas públicas (Etapa 6 da revisão de UX): GET /api/stats.

Rota fina e SOMENTE LEITURA, igual ao padrão de routes/me.py: nenhuma regra de negócio aqui, só
chama services/stats.py. Deliberadamente SEM autenticação -- é a mesma contagem pra qualquer
visitante, logado ou não, porque é informação do PRODUTO, não de uma conta.
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database.session import get_session
from services.stats import get_total_completed_generations

router = APIRouter(prefix="/api", tags=["stats"])


class StatsOut(BaseModel):
    total_generations: int


@router.get("/stats", response_model=StatsOut)
def get_stats(db: Session = Depends(get_session)) -> StatsOut:
    return StatsOut(total_generations=get_total_completed_generations(db))
