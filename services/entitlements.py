"""Entitlements (períodos de acesso pago): plano vigente, empilhamento e revogação.

Regras:
- Entitlement começa agora, ou quando termina o último acesso concedido do usuário (empilhamento):
  nenhum dia comprado se perde e os períodos nunca se sobrepõem.
- Só entram no cálculo os acessos `granted` e ainda não expirados. Revogado nunca volta a valer.
- Sobreposição entre acessos é INCONSISTÊNCIA de dados: registra erro e recusa decidir. Nunca
  escolhemos um plano "vencedor".
- Revogação realinha só os acessos FUTUROS válidos (nunca move o vigente), numa transação.
- Toda escrita usa lock_user_row (serialização por usuário). Nenhuma regra de pagamento aqui: o
  serviço de pagamento (etapa futura) apenas chamará grant_entitlement e revoke_entitlement.
"""
import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database.models import Entitlement, Payment
from services import entitlement_chain as chain
from services.locks import UserNotFoundError, lock_user_row  # noqa: F401  (UserNotFoundError reexportado)
from services.plans import get_plan
from utils.time_sp import resolve_now

logger = logging.getLogger("minhoca")


class EntitlementError(Exception):
    """Erro de regra de entitlement."""


class EntitlementInconsistencyError(EntitlementError):
    """Sobreposição entre entitlements concedidos: dado inconsistente, sem decisão silenciosa."""

    def __init__(self, user_id: int, entitlement_ids: tuple[int, ...]) -> None:
        self.user_id = user_id
        self.entitlement_ids = entitlement_ids
        super().__init__(f"Entitlements sobrepostos para o usuário {user_id}: ids {list(entitlement_ids)}")


class EntitlementNotFoundError(EntitlementError):
    pass


class InvalidEntitlementRequestError(EntitlementError):
    pass


@dataclass(frozen=True)
class RevocationResult:
    entitlement_id: int
    already_revoked: bool
    realigned_ids: tuple[int, ...]
    inconsistent: bool  # True: havia sobreposição; revogou mas NÃO realinhou (erro registrado)


def _valid_granted(db: Session, user_id: int, now: datetime) -> list[Entitlement]:
    """Acessos concedidos e ainda não expirados (vigentes + futuros), na ordem de início."""
    return list(
        db.execute(
            select(Entitlement)
            .where(
                Entitlement.user_id == user_id,
                Entitlement.status == "granted",
                Entitlement.expires_at > now,
            )
            .order_by(Entitlement.starts_at, Entitlement.id)
            .execution_options(populate_existing=True)  # sempre o estado atual do banco (após o lock)
        )
        .scalars()
        .all()
    )


def _pair_ids(pairs) -> tuple[int, ...]:
    return tuple(sorted({item.id for pair in pairs for item in pair}))


def _log_inconsistency(user_id: int, pairs) -> None:
    logger.error(
        "[ENTITLEMENT] INCONSISTÊNCIA: sobreposição entre entitlements concedidos user_id=%s ids=%s",
        user_id,
        list(_pair_ids(pairs)),
    )


def get_current_entitlement(db: Session, user_id: int, now: datetime | None = None) -> Entitlement | None:
    """Acesso vigente agora ([starts_at, expires_at)), ou None (usuário Free).

    Levanta EntitlementInconsistencyError se houver sobreposição que envolva um acesso vigente.
    Sobreposição só entre acessos futuros não impede a decisão de hoje: é registrada em log.
    """
    now = resolve_now(now)
    items = _valid_granted(db, user_id, now)
    blocking = chain.overlaps_involving_current(items, now)
    if blocking:
        _log_inconsistency(user_id, blocking)
        raise EntitlementInconsistencyError(user_id, _pair_ids(blocking))
    future_only = [pair for pair in chain.find_overlaps(items) if pair not in blocking]
    if future_only:
        _log_inconsistency(user_id, future_only)
    for item in items:
        if chain.is_current(item, now):
            return item
    return None


def compute_next_window(
    db: Session, user_id: int, duration_days: int, now: datetime | None = None
) -> tuple[datetime, datetime]:
    """(starts_at, expires_at) que um novo acesso teria agora (empilhamento). Só leitura."""
    now = resolve_now(now)
    items = _valid_granted(db, user_id, now)
    overlaps = chain.find_overlaps(items)
    if overlaps:
        _log_inconsistency(user_id, overlaps)
        raise EntitlementInconsistencyError(user_id, _pair_ids(overlaps))
    return chain.next_window(items, now, duration_days)


def grant_entitlement(
    db: Session, *, user_id: int, payment_id: int, plan_code: str, now: datetime | None = None
) -> Entitlement:
    """Concede um acesso pago, empilhando depois do último. Idempotente por payment_id."""
    plan = get_plan(plan_code)
    if not plan.paid:
        raise InvalidEntitlementRequestError("O plano Free não gera entitlement.")
    now = resolve_now(now)
    try:
        lock_user_row(db, user_id)

        payment = db.execute(select(Payment).where(Payment.id == payment_id)).scalar_one_or_none()
        if payment is None or payment.user_id != user_id:
            raise InvalidEntitlementRequestError("Este pagamento pertence a outro usuário.")

        existing = db.execute(select(Entitlement).where(Entitlement.payment_id == payment_id)).scalar_one_or_none()
        if existing is not None:
            db.commit()  # nada a gravar: só libera o lock
            return existing

        start, end = compute_next_window(db, user_id, plan.duration_days, now)
        entitlement = Entitlement(
            user_id=user_id,
            plan_code=plan.code,
            payment_id=payment_id,
            duration_days=plan.duration_days,
            starts_at=start,
            expires_at=end,
            status="granted",
        )
        db.add(entitlement)
        db.flush()
        db.commit()
        logger.info("[ENTITLEMENT] concedido user_id=%s plano=%s id=%s", user_id, plan.code, entitlement.id)
        return entitlement
    except IntegrityError:
        # Só ocorreria com dois processos concedendo o mesmo pagamento sem o lock: idempotência do banco.
        db.rollback()
        existing = db.execute(select(Entitlement).where(Entitlement.payment_id == payment_id)).scalar_one_or_none()
        if existing is None:
            raise
        db.commit()
        return existing
    except Exception:
        db.rollback()
        raise


def revoke_entitlement(
    db: Session, *, entitlement_id: int, reason: str, now: datetime | None = None
) -> RevocationResult:
    """Revoga um acesso (reembolso total ou chargeback, decididos pelo serviço de pagamento futuro)
    e realinha os acessos futuros válidos. Tudo em UMA transação. Idempotente.

    - O acesso revogado nunca volta a ser válido e não participa do realinhamento.
    - O acesso vigente nunca é movido. Só os futuros válidos são puxados para fechar o buraco.
    - Se houver sobreposição, a revogação É feita (o acesso precisa acabar), o realinhamento é
      pulado e o erro é registrado (`inconsistent=True`).
    """
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 32:
        raise InvalidEntitlementRequestError("O motivo da revogação deve ter de 1 a 32 caracteres.")
    now = resolve_now(now)
    owner = db.execute(select(Entitlement.user_id).where(Entitlement.id == entitlement_id)).scalar_one_or_none()
    if owner is None:
        db.rollback()
        raise EntitlementNotFoundError(f"Entitlement {entitlement_id} não encontrado.")
    try:
        lock_user_row(db, owner)
        entitlement = db.execute(
            select(Entitlement).where(Entitlement.id == entitlement_id).execution_options(populate_existing=True)
        ).scalar_one()
        if entitlement.status == "revoked":
            db.commit()
            return RevocationResult(entitlement.id, True, (), False)

        entitlement.status = "revoked"
        entitlement.revoked_at = now
        entitlement.revoke_reason = reason
        db.flush()

        items = _valid_granted(db, owner, now)  # o revogado já não entra (status mudou)
        overlaps = chain.find_overlaps(items)
        if overlaps:
            _log_inconsistency(owner, overlaps)
            db.commit()
            return RevocationResult(entitlement.id, False, (), True)

        changes = chain.realign_future(items, now)
        for item, new_start, new_end in changes:
            item.starts_at = new_start
            item.expires_at = new_end
        db.flush()

        leftover = chain.find_overlaps(_valid_granted(db, owner, now))
        if leftover:  # não deveria acontecer: desfaz tudo
            _log_inconsistency(owner, leftover)
            raise EntitlementInconsistencyError(owner, _pair_ids(leftover))

        db.commit()
        realigned = tuple(item.id for item, _, _ in changes)
        logger.info(
            "[ENTITLEMENT] revogado id=%s user_id=%s motivo=%s realinhados=%s",
            entitlement.id, owner, reason, list(realigned),
        )
        return RevocationResult(entitlement.id, False, realigned, False)
    except Exception:
        db.rollback()
        raise
