"""Camada fina de composição para o histórico de gerações do usuário autenticado (Etapa 7 da
revisão de UX, aprovação do CÉREBRO -- "meus últimos downloads").

Fluxo:

    routes/me.py -> services/generation_history.py -> services.usage + services.result_storage

Mesmo motivo de existir que services/user_status.py (ver o docstring daquele módulo, que se aplica
idêntico aqui): tests/test_usage_architecture.py::test_nenhuma_regra_de_cota_em_rotas_main_ou_frontend
proíbe qualquer rota fora de USAGE_ALLOWED_CONSUMERS de importar services.usage diretamente. Este
módulo não está em BUSINESS (services/plans.py, services/entitlement_chain.py,
services/entitlements.py, services/locks.py, services/usage.py) -- só traduz `Generation` (modelo
do banco) para uma estrutura mínima de exibição, sem decidir nenhuma regra de cota/validade nova --
por isso routes/me.py pode importar `services.generation_history` sem violar essa proteção.

`downloadable` reaproveita EXATAMENTE os mesmos critérios de
services.usage.get_downloadable_generation (status completed, output_storage_key presente,
output_expires_at presente e ainda não vencido) mais a checagem de arquivo físico
(services.result_storage.exists) que get_downloadable_batch_generations já faz para o ZIP de
lote -- nunca uma regra nova ou mais frouxa que a que já decide se o download de verdade funciona.
"""
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from services import result_storage
from services.usage import list_recent_generations
from utils.time_sp import resolve_now


@dataclass(frozen=True)
class GenerationHistoryItem:
    """Estrutura mínima de repasse para routes/me.py -- nunca o ORM Generation inteiro (que
    carrega campos internos como entitlement_id, request_id, batch_id, sem relação com o que o
    histórico do usuário precisa mostrar)."""

    id: int
    created_at: datetime
    platform: str | None
    status: str
    downloadable: bool
    display_filename: str | None


def get_generation_history(db: Session, user_id: int, *, limit: int = 20) -> list[GenerationHistoryItem]:
    """Compõe list_recent_generations(db, user_id, limit=limit) com a checagem de
    disponibilidade de cada item -- nenhuma decisão nova aqui, só delegação e montagem."""
    agora = resolve_now(None)
    itens = []
    for generation in list_recent_generations(db, user_id, limit=limit):
        extensao = generation.output_extension or "mp4"
        disponivel = (
            generation.status == "completed"
            and generation.output_storage_key is not None
            and generation.output_expires_at is not None
            and agora < generation.output_expires_at
            and result_storage.exists(generation.output_storage_key, extensao)
        )
        itens.append(
            GenerationHistoryItem(
                id=generation.id,
                created_at=generation.created_at,
                platform=generation.platform,
                status=generation.status,
                downloadable=disponivel,
                display_filename=generation.display_filename,
            )
        )
    return itens
