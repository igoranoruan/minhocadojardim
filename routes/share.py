"""Rotas do atalho "Enviar para o KLANGO" autenticadas pela SESSÃO normal do site (cookie).

Gerar/consultar/revogar o token pessoal, e gerenciar a fila (listar, remover item, limpar,
enviar para o lote) -- todas exigem login, exatamente como o resto da conta do usuário.

A ÚNICA rota pensada para ser chamada pelo atalho do celular (POST /api/ext/share, autenticada por
TOKEN, nunca por cookie) fica num arquivo/prefixo SEPARADO -- ver routes/share_ext.py -- de
propósito: isso mantém estas rotas aqui protegidas pelo OriginProtectionMiddleware normalmente
(CSRF), sem precisar isentar nada que use cookie.

Rota fina, mesmo padrão do resto do projeto: toda regra de negócio mora em services/share_queue.py.
"""
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database.models import User
from database.session import get_session
from routes.deps import get_current_user
from services.audio_usage import get_audio_allowance
from services.share_queue import (
    SendToBatchResult,
    clear_queue,
    generate_token,
    get_token_status,
    list_queue,
    remove_item,
    revoke_token,
    send_to_batch,
)

router = APIRouter(prefix="/api/share", tags=["share"])

NO_STORE = {"Cache-Control": "no-store"}


# ------------------------------------------------------------------------------ token
class ShareTokenStatusOut(BaseModel):
    exists: bool
    created_at: str | None = None
    last_used_at: str | None = None


class ShareTokenGeneratedOut(BaseModel):
    token: str  # devolvido só NESTA resposta -- o banco não guarda o valor puro


@router.get("/token", response_model=ShareTokenStatusOut)
def get_share_token(user: User = Depends(get_current_user), db: Session = Depends(get_session)) -> ShareTokenStatusOut:
    status = get_token_status(db, user)
    if status is None:
        return ShareTokenStatusOut(exists=False)
    return ShareTokenStatusOut(
        exists=True,
        created_at=status.created_at.isoformat(),
        last_used_at=status.last_used_at.isoformat() if status.last_used_at else None,
    )


@router.post("/token", response_model=ShareTokenGeneratedOut)
def post_share_token(user: User = Depends(get_current_user), db: Session = Depends(get_session)) -> ShareTokenGeneratedOut:
    """Gera um token NOVO -- revoga automaticamente o anterior, se houver (1 token ativo por
    usuário, aprovação do CÉREBRO). O valor devolvido aqui NUNCA pode ser recuperado depois."""
    token = generate_token(db, user)
    return ShareTokenGeneratedOut(token=token)


@router.delete("/token", status_code=204)
def delete_share_token(user: User = Depends(get_current_user), db: Session = Depends(get_session)) -> None:
    revoke_token(db, user)


# ------------------------------------------------------------------------------ fila
class ShareQueueItemOut(BaseModel):
    id: int
    url: str
    platform: str
    created_at: str


class ShareQueueListOut(BaseModel):
    items: list[ShareQueueItemOut]


@router.get("/queue", response_model=ShareQueueListOut)
def get_share_queue(user: User = Depends(get_current_user), db: Session = Depends(get_session)) -> ShareQueueListOut:
    itens = list_queue(db, user)
    return ShareQueueListOut(
        items=[
            ShareQueueItemOut(id=item.id, url=item.url, platform=item.platform, created_at=item.created_at.isoformat())
            for item in itens
        ]
    )


@router.delete("/queue/{item_id}", status_code=204)
def delete_share_queue_item(
    item_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_session),
) -> None:
    """Idempotente de propósito (mesmo padrão de revoke_session): remover um item que já não está
    pendente, ou que não é do usuário, não é erro -- simplesmente não há nada a fazer."""
    remove_item(db, user, item_id)


@router.delete("/queue", status_code=204)
def delete_share_queue(user: User = Depends(get_current_user), db: Session = Depends(get_session)) -> None:
    clear_queue(db, user)


class SendToBatchOut(BaseModel):
    urls: list[str]
    sent_count: int
    remaining_pending: int
    max_batch_size: int


@router.post("/queue/send-to-batch", response_model=SendToBatchOut)
def post_send_to_batch(
    formato: Literal["video", "audio"] = "video",
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> SendToBatchOut:
    """Separa até o teto do plano (Plan.max_batch_size) e devolve as URLs para o FRONTEND
    preencher o formulário de lote já existente -- não cria lote/reserva cota aqui (ver docstring
    de services.share_queue.send_to_batch): a confirmação final continua sendo POST
    /api/download-batch (vídeo) ou /api/audio/batch (áudio), inalterados.

    `formato` (17/10/2026, ajuste pedido pelo CÉREBRO -- botão "Extrair áudios"): default "video"
    preserva o comportamento de sempre (nenhuma mudança para quem já usa a fila para vídeo).
    "audio" calcula o saldo diário de áudio restante AGORA, no servidor (nunca confia num número
    vindo do cliente), e passa como teto adicional a send_to_batch -- assim a fila nunca separa
    (e marca 'consumed') mais itens do que o saldo de áudio realmente aceita; o que não couber
    continua pendente na fila, em vez de ser perdido se /api/audio/batch rejeitasse o lote inteiro
    por falta de saldo (reserve_audio_batch é tudo-ou-nada, ver services/audio_usage.py)."""
    max_items = None
    if formato == "audio":
        allowance = get_audio_allowance(db, user.id)
        # unlimited (admin, ver services/audio_usage.py::AudioAllowance.unlimited): `remaining`
        # pode estar em 0 mesmo a reserva não bloqueando mais essa conta -- não impor teto extra
        # aqui, mesmo padrão de isenção já usado no resto da camada de áudio.
        max_items = None if allowance.unlimited else allowance.remaining
    resultado: SendToBatchResult = send_to_batch(db, user, max_items=max_items)
    return SendToBatchOut(
        urls=resultado.urls,
        sent_count=resultado.sent_count,
        remaining_pending=resultado.remaining_pending,
        max_batch_size=resultado.max_batch_size,
    )
