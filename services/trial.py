"""Orquestração do "Teste VIP de 24h" por link de campanha (ETAPA B7, pedido e aprovação do Igor,
29/10/2026): POST /api/trial/redeem chama só redeem_trial, abaixo -- nenhuma outra rota escreve em
`trial_redemptions` nem chama grant_trial_entitlement.

NÃO é BUSINESS (services/plans.py, services/entitlement_chain.py, services/entitlements.py,
services/locks.py, services/usage.py) -- mesmo papel de services/referrals.py: orquestra travas e
delega a concessão real do acesso para services/entitlements.py::grant_trial_entitlement, que é
quem de fato grava a tabela que decide acesso (`entitlements`). Este módulo só grava o ledger de
abuso/medição (`trial_redemptions`) e as travas específicas do pedido -- nenhuma delas protege a
tabela em si (isso é grant_trial_entitlement: qualquer entitlement anterior já recusa, ver seu
docstring), protegem o ENDPOINT contra abuso.

Ordem das travas (mais barata/sem banco primeiro, pedido do Igor: "mensagens de recusa curtas e
educadas, sem explicar qual trava pegou" -- por isso TODAS as travas de abuso levantam o MESMO
TrialNotEligibleError, nunca uma exceção por trava):
1. Formato da campanha (regex, sem acesso a banco).
2. Domínio de e-mail descartável (sem acesso a banco -- já temos o e-mail do usuário autenticado).
3. Rate limit em memória por usuário (mesmo padrão de services/pinterest_board.py).
4. IP: no máximo TRIAL_MAX_REDEMPTIONS_PER_IP resgates nos últimos TRIAL_IP_WINDOW_DAYS dias.
5. Aparelho: já existe um resgate com este device_hash? (qualquer usuário, qualquer data).
6. A trava estrutural final (conta/pagante/empilhamento) é só dentro de
   grant_trial_entitlement -- TrialAlreadyGrantedError também vira TrialNotEligibleError aqui.

IP e device_id NUNCA gravados crus -- só o hash (utils.security.hash_ip/hash_device_id, MESMO
segredo settings.ip_hash_secret, rótulos HMAC diferentes)."""
import logging
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from config import (
    TRIAL_CAMPAIGN_CODE_MAX_LENGTH,
    TRIAL_DEVICE_ID_MAX_LENGTH,
    TRIAL_DISPOSABLE_EMAIL_DOMAINS,
    TRIAL_IP_WINDOW_DAYS,
    TRIAL_MAX_REDEMPTIONS_PER_IP,
    TRIAL_REDEEM_RATE_LIMIT_PER_HOUR,
    get_settings,
)
from database.models import Entitlement, TrialRedemption, User
from services.entitlements import TrialAlreadyGrantedError, grant_trial_entitlement
from services.plans import TRIAL_VIP_PLAN
from utils.security import hash_device_id, hash_ip
from utils.time_sp import resolve_now

logger = logging.getLogger("minhoca")

_ONE_HOUR_SECONDS = 60 * 60
_CAMPAIGN_CODE_RE = re.compile(rf"^[A-Za-z0-9-]{{1,{TRIAL_CAMPAIGN_CODE_MAX_LENGTH}}}$")


class TrialError(Exception):
    """Base dos erros desta orquestração."""

    user_message = "Não foi possível ativar o teste agora."
    status_code = 422

    def __init__(self, detail: str | None = None) -> None:
        self.detail = detail or self.user_message
        super().__init__(self.detail)


class InvalidCampaignCodeError(TrialError):
    """`?c=` ausente ou fora do formato (letras/números/hífen, até TRIAL_CAMPAIGN_CODE_MAX_LENGTH
    caracteres) -- diferente das travas de ABUSO abaixo, esta é validação de entrada (erro do
    link, não tentativa de burlar nada), então pode ter mensagem específica."""

    user_message = "Link de campanha inválido."


class TrialNotEligibleError(TrialError):
    """TODAS as travas de abuso (conta já tem/teve entitlement, aparelho já usado, limite de IP,
    e-mail descartável) caem aqui -- MESMA mensagem genérica e educada para todas, pedido
    explícito do Igor ("sem explicar qual trava pegou"). 422, nunca 403/409 (não é um erro de
    permissão nem de conflito de estado visível: do ponto de vista de quem pergunta, é só "esta
    conta/aparelho/IP não tem direito ao teste agora")."""

    user_message = "Esta conta não tem direito ao teste VIP no momento."


class TrialRedeemRateLimitedError(TrialError):
    status_code = 429

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("Muitas tentativas em pouco tempo. Tente novamente mais tarde.")
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True)
class TrialRedemptionResult:
    entitlement_id: int
    expires_at: datetime


# Rate limit EM MEMÓRIA (mesma justificativa/padrão de services/pinterest_board.py: 1 única
# instância Render, 1 único processo uvicorn -- não precisa sobreviver a redeploy nem coordenar
# entre processos). Protege o ENDPOINT contra martelamento; a trava de negócio de verdade (1 por
# conta/aparelho/IP) está sempre no banco, abaixo.
_lock = threading.Lock()
_attempts_by_user: dict[int, list[float]] = {}


def _check_rate_limit(user_id: int) -> None:
    now = time.monotonic()
    window_start = now - _ONE_HOUR_SECONDS
    with _lock:
        attempts = [t for t in _attempts_by_user.get(user_id, []) if t > window_start]
        if len(attempts) >= TRIAL_REDEEM_RATE_LIMIT_PER_HOUR:
            retry_after = max(1, int(attempts[0] + _ONE_HOUR_SECONDS - now))
            _attempts_by_user[user_id] = attempts
            raise TrialRedeemRateLimitedError(retry_after)
        attempts.append(now)
        _attempts_by_user[user_id] = attempts


def validate_campaign_code(campaign: str | None) -> str:
    if not isinstance(campaign, str) or not _CAMPAIGN_CODE_RE.fullmatch(campaign):
        raise InvalidCampaignCodeError()
    return campaign


def _is_disposable_email(email: str) -> bool:
    _, _, domain = email.partition("@")
    return domain.strip().lower() in TRIAL_DISPOSABLE_EMAIL_DOMAINS


def _count_redemptions_for_ip(db: Session, ip_hash: str, now: datetime) -> int:
    window_start = now - timedelta(days=TRIAL_IP_WINDOW_DAYS)
    return db.execute(
        select(func.count())
        .select_from(TrialRedemption)
        .where(TrialRedemption.ip_hash == ip_hash, TrialRedemption.created_at > window_start)
    ).scalar_one()


def _device_already_redeemed(db: Session, device_hash: str) -> bool:
    return (
        db.execute(
            select(TrialRedemption.id).where(TrialRedemption.device_hash == device_hash).limit(1)
        ).scalar_one_or_none()
        is not None
    )


def redeem_trial(
    db: Session,
    *,
    user: User,
    campaign: str,
    device_id: str,
    ip: str | None,
    now: datetime | None = None,
) -> TrialRedemptionResult:
    """Ponto de entrada único desta etapa (chamado por routes/trial.py).

    `device_id` já validado quanto ao TAMANHO pela rota (Pydantic, TRIAL_DEVICE_ID_MAX_LENGTH) --
    o valor em si (gerado pelo frontend) nunca é interpretado aqui, só hasheado. `ip` pode ser
    None (sem client.host -- mesma situação já tratada em routes/deps.py::client_ip); quando None,
    a trava de IP simplesmente não se aplica a esta chamada (nunca um hash de string vazia)."""
    campaign = validate_campaign_code(campaign)
    if not isinstance(device_id, str) or not device_id.strip() or len(device_id) > TRIAL_DEVICE_ID_MAX_LENGTH:
        raise TrialNotEligibleError()
    if _is_disposable_email(user.email):
        raise TrialNotEligibleError()
    _check_rate_limit(user.id)

    now = resolve_now(now)
    cfg = get_settings()
    device_hash = hash_device_id(device_id, cfg.ip_hash_secret)
    ip_hash = hash_ip(ip, cfg.ip_hash_secret) if ip else None

    if _device_already_redeemed(db, device_hash):
        raise TrialNotEligibleError()
    if ip_hash is not None and _count_redemptions_for_ip(db, ip_hash, now) >= TRIAL_MAX_REDEMPTIONS_PER_IP:
        raise TrialNotEligibleError()

    try:
        entitlement = grant_trial_entitlement(db, user_id=user.id, now=now)
    except TrialAlreadyGrantedError:
        raise TrialNotEligibleError() from None

    # Ledger de abuso/medição -- best-effort (mesma filosofia de services/referrals.py::
    # process_entitlement_granted: o acesso JÁ foi concedido com sucesso acima; uma falha aqui
    # nunca desfaz isso, só fica sem uma linha de auditoria para esta campanha específica, o que é
    # preferível a um usuário pagar o preço de um erro puramente de medição).
    try:
        db.add(
            TrialRedemption(
                user_id=user.id,
                entitlement_id=entitlement.id,
                campaign=campaign,
                ip_hash=ip_hash,
                device_hash=device_hash,
                created_at=now,
            )
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.error(
            "[TRIAL] entitlement concedido (id=%s) mas falha ao gravar trial_redemptions: %s",
            entitlement.id, exc,
        )

    logger.info("[TRIAL] resgatado user_id=%s campanha=%s entitlement_id=%s", user.id, campaign, entitlement.id)
    return TrialRedemptionResult(entitlement_id=entitlement.id, expires_at=entitlement.expires_at)
