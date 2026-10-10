"""Orquestração da listagem de pasta do Pinterest (Etapa 3, 10/10/2026 -- aprovação do CÉREBRO):
quem pode chamar (plano com lote habilitado) + rate limit por usuário + delega a extração em si
para download/board_listing.py (a única camada que conhece yt-dlp, ver docstring de lá).

Nada aqui consome cota de geração/lote -- listar não baixa vídeo nenhum; a cota continua sendo
gasta só em /api/download-batch, quando o usuário efetivamente escolhe o que baixar (etapa futura
do frontend). Por isso esta orquestração não usa services/locks.py nem grava nada em `batches` --
a única proteção aqui é o rate limit abaixo, para o IP do Render, não para a cota financeira do
usuário.
"""
import logging
import threading
import time

from sqlalchemy.orm import Session

from config import BOARD_LIST_RATE_LIMIT_PER_HOUR
from database.models import User
from download.board_listing import BoardListing, list_board_pins
from download.errors import DownloadError
from services.entitlements import get_current_entitlement
from services.plans import FREE_PLAN, get_plan

logger = logging.getLogger("minhoca")

_ONE_HOUR_SECONDS = 60 * 60


class PinterestBoardError(Exception):
    """Base dos erros desta orquestração -- nunca a mensagem crua do yt-dlp chega ao usuário."""

    user_message = "Não foi possível listar esta pasta agora."
    status_code = 422

    def __init__(self, detail: str | None = None) -> None:
        self.detail = detail or self.user_message
        super().__init__(self.detail)


class BoardListingPlanNotAllowedError(PinterestBoardError):
    """Plano do usuário não tem lote habilitado (Free) -- MESMO gate server-side de
    /api/download-batch (services/usage.py::reserve_batch), nunca confiando só na UI escondendo
    o botão (a tela de seleção, etapa futura do frontend, pode até mostrar a opção bloqueada com
    CTA de upgrade -- pedido do CÉREBRO -- mas o backend recusa de qualquer jeito)."""

    user_message = "Listar pastas do Pinterest é um recurso dos planos com lote."
    status_code = 403


class BoardListingRateLimitedError(PinterestBoardError):
    user_message = "Muitas listagens em pouco tempo. Tente novamente mais tarde."
    status_code = 429

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__(self.user_message)
        self.retry_after_seconds = retry_after_seconds


# Rate limit EM MEMÓRIA (ver justificativa completa em config.py, ao lado de
# BOARD_LIST_RATE_LIMIT_PER_HOUR): a aplicação roda em 1 única instância Render, 1 único processo
# uvicorn -- não há múltiplos processos para coordenar, e este contador não precisa sobreviver a um
# redeploy (diferente da cota financeira, que mora no banco). `_lock` protege contra duas
# requisições do MESMO usuário em threads concorrentes do único processo.
_lock = threading.Lock()
_attempts_by_user: dict[int, list[float]] = {}


def _check_rate_limit(user_id: int) -> None:
    now = time.monotonic()
    window_start = now - _ONE_HOUR_SECONDS
    with _lock:
        attempts = [t for t in _attempts_by_user.get(user_id, []) if t > window_start]
        if len(attempts) >= BOARD_LIST_RATE_LIMIT_PER_HOUR:
            retry_after = max(1, int(attempts[0] + _ONE_HOUR_SECONDS - now))
            _attempts_by_user[user_id] = attempts  # já aproveita e limpa os expirados
            raise BoardListingRateLimitedError(retry_after)
        attempts.append(now)
        _attempts_by_user[user_id] = attempts


def list_board(db: Session, user: User, url: str) -> BoardListing:
    """Ponto de entrada único desta etapa (chamado por routes/pinterest.py).

    Ordem: (1) plano permite lote? (2) rate limit do usuário OK? (3) delega a extração real.
    Nessa ordem de propósito -- recusar por plano é mais barato (nenhum acesso de rede) do que
    gastar uma tentativa do rate limit num usuário que nem teria acesso à tela."""
    entitlement = get_current_entitlement(db, user.id)
    plan = FREE_PLAN if entitlement is None else get_plan(entitlement.plan_code)
    if not plan.batch_enabled:
        raise BoardListingPlanNotAllowedError()

    _check_rate_limit(user.id)

    try:
        return list_board_pins(url)
    except DownloadError as exc:
        logger.info("[PINTEREST_BOARD] listagem recusada para usuário=%s: %s", user.id, exc.__class__.__name__)
        raise PinterestBoardError(exc.user_message) from None
