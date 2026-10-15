"""Orquestração do atalho "Enviar para o KLANGO" (15/10/2026, aprovação do CÉREBRO).

Duas responsabilidades, deliberadamente separadas:
1. Token pessoal (gerar/consultar/revogar) -- autentica SÓ `add_to_queue` (POST /api/ext/share),
   nunca nenhuma outra rota. Ver database/models/share_token.py para a invariável "1 token ativo
   por usuário", garantida pelo banco.
2. Fila (adicionar/listar/remover/limpar/enviar para o lote) -- autenticada pela SESSÃO normal do
   site (cookie), exceto `add_to_queue`, que é a única operação pensada para ser chamada pelo
   atalho do celular (token).

NADA aqui consome cota de geração/lote: a fila só guarda URLs normalizadas e validadas. O download
de verdade continua acontecendo só em /api/download-batch (services/generation_flow.py::run_batch,
inalterado) -- `send_to_batch` apenas separa até N itens pendentes (N = Plan.max_batch_size do
usuário) e devolve as URLs prontas para o FRONTEND preencher o formulário de lote já existente; o
usuário ainda confirma o envio normalmente, com a MESMA checagem de cota de sempre. Isso evita
duplicar reserve_batch aqui (nunca reimplementamos a reserva de cota numa segunda camada).

Reaproveita, sem alterar: download.url_safety.validate_url + download.platform.detect_platform
(mesmo validador do download real -- ver download/service.py), utils.security.generate_share_token/
hash_share_token (token namespace PRÓPRIO, nunca o de sessão), e o padrão de rate limit em memória
já aprovado em services/pinterest_board.py (1 única instância Render).
"""
import logging
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from config import (
    SHARE_QUEUE_MAX_PENDING,
    SHARE_QUEUE_RATE_LIMIT_PER_HOUR,
    SHARE_TRACKING_PARAMS,
)
from database.models import ShareQueueItem, ShareToken, User
from database.types import utcnow
from download.errors import DownloadError
from download.platform import detect_platform
from download.url_safety import validate_url
from services.entitlements import get_current_entitlement
from services.plans import FREE_PLAN, get_plan
from utils.security import generate_share_token, hash_share_token

logger = logging.getLogger("minhoca")

_ONE_HOUR_SECONDS = 60 * 60
_URL_IN_TEXT_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_TRAILING_PUNCTUATION = ".,;:!?)]}>\"'"


# ------------------------------------------------------------------------------ erros
class ShareQueueError(Exception):
    """Base -- hoje só InvalidShareTokenError precisa de handler HTTP global (ver main.py);
    todo o resto (link inválido, fila cheia, rate limit, duplicado) vira uma mensagem de TEXTO
    amigável no resultado de `add_to_queue`, nunca uma exceção -- o atalho mostra como notificação,
    não como erro de rede (ver routes/share_ext.py)."""


class InvalidShareTokenError(ShareQueueError):
    """Token ausente, com formato inválido, não encontrado ou revogado -- SEMPRE a mesma mensagem
    genérica (nunca diferencia "não existe" de "revogado", mesma filosofia dos 404 genéricos já
    usados em routes/generations.py)."""

    status_code = 401
    detail = "Token inválido ou revogado."


# ------------------------------------------------------------------------------ token
@dataclass(frozen=True)
class ShareTokenStatus:
    created_at: datetime
    last_used_at: datetime | None


def get_token_status(db: Session, user: User) -> ShareTokenStatus | None:
    """Estado do token ATIVO do usuário, se houver -- NUNCA o valor do token em si (só existe em
    texto puro no instante de generate_token, que o devolve uma única vez)."""
    token = db.execute(
        select(ShareToken).where(ShareToken.user_id == user.id, ShareToken.revoked_at.is_(None))
    ).scalar_one_or_none()
    if token is None:
        return None
    return ShareTokenStatus(created_at=token.created_at, last_used_at=token.last_used_at)


def generate_token(db: Session, user: User) -> str:
    """Gera um token NOVO, revogando o anterior (se houver) na MESMA transação -- "1 token ativo
    por usuário" (aprovação do CÉREBRO). Devolve o valor em texto puro, que não é recuperável
    depois (o banco só guarda o hash) -- quem chama é responsável por mostrá-lo ao usuário UMA
    única vez."""
    now = utcnow()
    db.execute(
        update(ShareToken)
        .where(ShareToken.user_id == user.id, ShareToken.active_slot == 1)
        .values(active_slot=None, revoked_at=now)
    )
    token = generate_share_token()
    db.add(
        ShareToken(
            user_id=user.id,
            token_hash=hash_share_token(token),
            active_slot=1,
            created_at=now,
        )
    )
    db.commit()
    return token


def revoke_token(db: Session, user: User) -> bool:
    """Revoga o token ativo, se houver. Repetir a chamada é seguro: devolve False se já não havia
    token ativo (mesmo padrão de services.auth.revoke_session)."""
    result = db.execute(
        update(ShareToken)
        .where(ShareToken.user_id == user.id, ShareToken.active_slot == 1)
        .values(active_slot=None, revoked_at=utcnow())
    )
    db.commit()
    return result.rowcount == 1


def authenticate_share_token(db: Session, token: str | None) -> User | None:
    """Devolve o usuário dono do token ATIVO, ou None -- NUNCA lança (quem decide se vira 401 é
    routes/share_ext.py, levantando InvalidShareTokenError). `last_used_at` é atualizado em TODA
    chamada bem-sucedida, sem o debounce usado em AuthSession: este token é usado poucas vezes por
    dia (compartilhamentos manuais), nunca a cada carregamento de página, então o custo de uma
    escrita a mais por uso é desprezível."""
    if not token or len(token) > 512:
        return None
    found = db.execute(
        select(ShareToken, User)
        .join(User, User.id == ShareToken.user_id)
        .where(ShareToken.token_hash == hash_share_token(token))
    ).first()
    if found is None:
        return None
    share_token, user = found
    if share_token.revoked_at is not None:
        return None
    db.execute(
        update(ShareToken)
        .where(ShareToken.id == share_token.id, ShareToken.revoked_at.is_(None))
        .values(last_used_at=utcnow())
    )
    db.commit()
    return user


def extract_token_from_request(*, authorization_header: str | None, body_token: str | None) -> str | None:
    """Bearer header tem prioridade; `body_token` existe só por compatibilidade com o app Atalhos
    do iOS (campo "token" no corpo, pedido explícito do CÉREBRO -- "Obter Conteúdo de URL" do
    Atalhos permite cabeçalho customizado, mas alguns usuários preferem colar tudo no corpo)."""
    if authorization_header:
        scheme, _, value = authorization_header.partition(" ")
        if scheme.lower() == "bearer" and value.strip():
            return value.strip()
    if body_token and body_token.strip():
        return body_token.strip()
    return None


# ------------------------------------------------------------------------------ normalização de URL
def extract_url_from_text(text: str) -> str | None:
    """Primeira URL http(s) encontrada em `text` -- o compartilhamento às vezes manda um texto
    livre com o link dentro (ex.: legenda + link). Remove pontuação de fim de frase colada no
    final (".", ",", ")", etc.) que não faz parte da URL -- heurística simples, não cobre 100% dos
    casos (uma URL cujo último caractere REAL seja um desses símbolos perderia esse caractere;
    nenhuma plataforma suportada gera URLs assim hoje -- ver limitação no relatório da Etapa 2)."""
    match = _URL_IN_TEXT_RE.search(text or "")
    if match is None:
        return None
    url = match.group(0)
    return url.rstrip(_TRAILING_PUNCTUATION) or None


def normalize_share_url(url: str) -> str:
    """Remove só os parâmetros de SHARE_TRACKING_PARAMS (lista fechada, config.py) -- nunca um
    parâmetro desconhecido, nunca o path/fragmento. Preserva a ORDEM dos parâmetros restantes."""
    parts = urlsplit(url)
    pares = [(chave, valor) for chave, valor in parse_qsl(parts.query, keep_blank_values=True) if chave not in SHARE_TRACKING_PARAMS]
    nova_query = urlencode(pares)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, nova_query, parts.fragment))


# ------------------------------------------------------------------------------ rate limit (em memória)
# MESMA justificativa de services/pinterest_board.py::_attempts_by_user (1 única instância Render,
# 1 único processo uvicorn -- ver config.py, ao lado de SHARE_QUEUE_RATE_LIMIT_PER_HOUR).
_lock = threading.Lock()
_attempts_by_user: dict[int, list[float]] = {}


def _rate_limited(user_id: int) -> bool:
    now = time.monotonic()
    window_start = now - _ONE_HOUR_SECONDS
    with _lock:
        attempts = [t for t in _attempts_by_user.get(user_id, []) if t > window_start]
        if len(attempts) >= SHARE_QUEUE_RATE_LIMIT_PER_HOUR:
            _attempts_by_user[user_id] = attempts
            return True
        attempts.append(now)
        _attempts_by_user[user_id] = attempts
        return False


# ------------------------------------------------------------------------------ fila
@dataclass(frozen=True)
class ShareQueueOutcome:
    """Resultado de add_to_queue -- `message` já vem pronta para o atalho mostrar como notificação
    (texto curto, com emoji -- pedido explícito do CÉREBRO). `added` distingue só o caso de
    sucesso real, para quem quiser logar/medir sem fazer parsing de texto."""

    added: bool
    message: str
    pending_count: int


def add_to_queue(db: Session, user: User, *, url: str | None, text: str | None) -> ShareQueueOutcome:
    """Ponto de entrada único de POST /api/ext/share. Ordem (mais barato primeiro, mesma filosofia
    de services/pinterest_board.py::list_board): rate limit -> extrai/normaliza -> valida ->
    dedup -> limite de pendentes -> insere.

    Validação usa SÓ `validate_url` + `detect_platform` (NUNCA `resolve_redirect_chain`): seguir
    redirecionamento é uma chamada de rede (HEAD) síncrona, pensada para o momento do download real
    -- aqui o objetivo é um "adicionar à fila" instantâneo a partir de um atalho do celular. A
    validação completa (incluindo redirecionamentos) continua acontecendo de qualquer forma quando
    o usuário efetivamente confirma o lote em /api/download-batch (download/service.py::
    download_video) -- esta camada é só uma triagem antecipada, não a fronteira de segurança
    final (ver limitação no relatório da Etapa 2)."""
    if _rate_limited(user.id):
        return ShareQueueOutcome(False, "⚠️ Muitos links em pouco tempo. Tente de novo em instantes.", _pending_count(db, user))

    candidate = (url or "").strip() or extract_url_from_text(text or "")
    if not candidate:
        return ShareQueueOutcome(False, "❌ Link não encontrado.", _pending_count(db, user))

    normalized = normalize_share_url(candidate)
    try:
        validated = validate_url(normalized)
        platform = detect_platform(validated.url)
    except DownloadError:
        return ShareQueueOutcome(False, "❌ Link não suportado.", _pending_count(db, user))

    pending_count = _pending_count(db, user)
    if pending_count >= SHARE_QUEUE_MAX_PENDING:
        return ShareQueueOutcome(
            False, f"⚠️ Fila cheia ({SHARE_QUEUE_MAX_PENDING} links) — envie para o lote antes de adicionar mais.", pending_count,
        )

    already_pending = db.execute(
        select(ShareQueueItem.id).where(
            ShareQueueItem.user_id == user.id,
            ShareQueueItem.status == "pending",
            ShareQueueItem.url == validated.url,
        )
    ).first()
    if already_pending is not None:
        return ShareQueueOutcome(False, "⚠️ Link já estava na fila", pending_count)

    db.add(
        ShareQueueItem(
            user_id=user.id, url=validated.url, platform=platform.value, status="pending", created_at=utcnow(),
        )
    )
    db.commit()
    novo_total = pending_count + 1
    return ShareQueueOutcome(True, f"✅ Adicionado ao KLANGO ({novo_total} na fila)", novo_total)


def _pending_count(db: Session, user: User) -> int:
    return len(
        db.execute(
            select(ShareQueueItem.id).where(ShareQueueItem.user_id == user.id, ShareQueueItem.status == "pending")
        ).all()
    )


def list_queue(db: Session, user: User) -> list[ShareQueueItem]:
    """Itens pendentes, mais antigos primeiro (ordem de chegada -- mesma ordem que send_to_batch usa)."""
    return list(
        db.execute(
            select(ShareQueueItem)
            .where(ShareQueueItem.user_id == user.id, ShareQueueItem.status == "pending")
            .order_by(ShareQueueItem.created_at.asc(), ShareQueueItem.id.asc())
        ).scalars()
    )


def remove_item(db: Session, user: User, item_id: int) -> bool:
    """Remove (status='removed') 1 item pendente do próprio usuário. Devolve False se o item não
    existe, não é do usuário, ou já não está pendente -- nunca levanta (mesma filosofia de
    revoke_token/revoke_session: idempotente)."""
    result = db.execute(
        update(ShareQueueItem)
        .where(ShareQueueItem.id == item_id, ShareQueueItem.user_id == user.id, ShareQueueItem.status == "pending")
        .values(status="removed")
    )
    db.commit()
    return result.rowcount == 1


def clear_queue(db: Session, user: User) -> int:
    """Remove TODOS os itens pendentes do usuário. Devolve quantos foram removidos."""
    result = db.execute(
        update(ShareQueueItem)
        .where(ShareQueueItem.user_id == user.id, ShareQueueItem.status == "pending")
        .values(status="removed")
    )
    db.commit()
    return result.rowcount


@dataclass(frozen=True)
class SendToBatchResult:
    urls: list[str]
    sent_count: int
    remaining_pending: int
    max_batch_size: int


def send_to_batch(db: Session, user: User) -> SendToBatchResult:
    """Separa até `Plan.max_batch_size` itens pendentes (os MAIS ANTIGOS primeiro) e marca como
    'consumed' -- devolve as URLs para o FRONTEND preencher o formulário de lote já existente. NÃO
    chama reserve_batch/run_batch aqui: a reserva de cota e o download continuam acontecendo
    exclusivamente quando o usuário confirma o envio normal em POST /api/download-batch (nunca
    duplicamos essa lógica nesta camada -- ver docstring do módulo).

    Plano Free (max_batch_size=0, sem lote) devolve uma lista vazia -- a rota decide se isso vira
    aviso específico; `remaining_pending` deixa claro para o frontend que nada foi consumido."""
    entitlement = get_current_entitlement(db, user.id)
    plan = FREE_PLAN if entitlement is None else get_plan(entitlement.plan_code)
    total_pendente = _pending_count(db, user)

    if plan.max_batch_size <= 0:
        return SendToBatchResult(urls=[], sent_count=0, remaining_pending=total_pendente, max_batch_size=0)

    itens = list(
        db.execute(
            select(ShareQueueItem)
            .where(ShareQueueItem.user_id == user.id, ShareQueueItem.status == "pending")
            .order_by(ShareQueueItem.created_at.asc(), ShareQueueItem.id.asc())
            .limit(plan.max_batch_size)
        ).scalars()
    )
    if not itens:
        return SendToBatchResult(urls=[], sent_count=0, remaining_pending=0, max_batch_size=plan.max_batch_size)

    now = utcnow()
    ids = [item.id for item in itens]
    db.execute(
        update(ShareQueueItem).where(ShareQueueItem.id.in_(ids)).values(status="consumed", consumed_at=now)
    )
    db.commit()
    return SendToBatchResult(
        urls=[item.url for item in itens],
        sent_count=len(itens),
        remaining_pending=total_pendente - len(itens),
        max_batch_size=plan.max_batch_size,
    )
