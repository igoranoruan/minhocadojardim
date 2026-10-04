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
from datetime import datetime, timedelta

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
    """Concede um acesso pago. Idempotente por payment_id.

    TROCA IMEDIATA (decisão do CÉREBRO, 02/10/2026, depois de um upgrade real em produção expor o
    problema): se o usuário JÁ TEM um acesso vigente agora, o novo NÃO empilha depois dele -- o
    vigente é encerrado NESTE INSTANTE (expires_at = now; os dias que sobravam são descartados,
    sem crédito proporcional nem reembolso automático -- decisão explícita, simplicidade sobre
    proporcionalidade para o lançamento) e o novo começa já. O empilhamento antigo (ver
    entitlement_chain.next_window) só fazia sentido para RENOVAR o mesmo acesso antes de vencer;
    para upgrade, o cliente espera o benefício imediatamente, não numa fila.

    Qualquer acesso FUTURO já empilhado de uma compra anterior (não é o caso comum, mas é possível)
    é realinhado para começar só depois que este novo acesso acabar (entitlement_chain.
    realign_after) -- nenhum dia comprado de um acesso futuro é perdido, só adiado.

    Sem acesso vigente agora (usuário Free, ou só com acessos futuros pendentes): comportamento
    ORIGINAL preservado -- empilha depois do último acesso já concedido."""
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

        items = _valid_granted(db, user_id, now)
        overlaps = chain.find_overlaps(items)
        if overlaps:
            _log_inconsistency(user_id, overlaps)
            raise EntitlementInconsistencyError(user_id, _pair_ids(overlaps))
        current = next((item for item in items if chain.is_current(item, now)), None)

        if current is None:
            start, end = chain.next_window(items, now, plan.duration_days)
        else:
            # Guarda defensiva: is_current já garante now >= current.starts_at, mas nunca
            # encerramos um acesso no MESMO instante em que começou (violaria o CHECK
            # expires_at > starts_at) -- extremamente improvável (exigiria duas trocas no mesmo
            # microssegundo, já serializadas por lock_user_row), mas nunca gravado sem checar.
            current.expires_at = now if now > current.starts_at else current.starts_at + timedelta(seconds=1)
            db.flush()

            start, end = now, now + timedelta(days=plan.duration_days)

            futuros = [item for item in items if item.id != current.id]
            for item, new_start, new_end in chain.realign_after(futuros, end):
                item.starts_at = new_start
                item.expires_at = new_end
            db.flush()

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


def extend_entitlement(
    db: Session, *, user_id: int, entitlement_id: int, extra_days: int, now: datetime | None = None,
) -> Entitlement:
    """Etapa 8 (programa de indicação, aprovação do CÉREBRO): soma `extra_days` ao fim de um
    entitlement já concedido -- usada por services/referrals.py para aplicar a recompensa de
    indicação (dias extras de plano), NUNCA por pagamento algum (esta função não lê nem cria
    Payment, não tem relação com preço).

    Mesma segurança de grant_entitlement: trava o usuário, relê os acessos vigentes/futuros,
    recusa se houver sobreposição (EntitlementInconsistencyError, nunca decide um "vencedor"), e
    realinha qualquer acesso futuro já empilhado depois do novo fim -- nenhum dia comprado é
    perdido, só adiado. `entitlement_id` precisa ser um acesso `granted` e NÃO expirado deste
    `user_id` nesse momento (achado em `_valid_granted`, a MESMA fonte que grant_entitlement já
    usa) -- um entitlement expirado ou revogado nunca é "reaberto" por uma recompensa; a
    recompensa pendente continua pendente até o indicador ter um acesso vigente de novo."""
    if extra_days <= 0:
        raise InvalidEntitlementRequestError("extra_days deve ser positivo.")
    now = resolve_now(now)
    try:
        lock_user_row(db, user_id)
        items = _valid_granted(db, user_id, now)
        overlaps = chain.find_overlaps(items)
        if overlaps:
            _log_inconsistency(user_id, overlaps)
            raise EntitlementInconsistencyError(user_id, _pair_ids(overlaps))

        entitlement = next((item for item in items if item.id == entitlement_id), None)
        if entitlement is None:
            raise InvalidEntitlementRequestError(
                f"Entitlement {entitlement_id} não é um acesso granted/vigente ou futuro de {user_id}."
            )

        fim_antigo = entitlement.expires_at
        entitlement.expires_at = fim_antigo + timedelta(days=extra_days)
        db.flush()

        # Só quem vem DEPOIS deste entitlement na cadeia (starts_at >= fim_antigo) pode ter sido
        # empilhado em seguida a ele -- qualquer outro item de `items` termina ANTES de
        # `entitlement` começar (nenhum overlap, já checado acima). Filtrar por "!= entitlement.id"
        # sozinho seria um bug: um entitlement JÁ ENCERRADO (ex.: o antigo "current" que
        # grant_entitlement acabou de clampar para expirar agora, numa troca imediata) nunca
        # aparece aqui (_valid_granted já exclui expires_at <= now), mas um FUTURO já empilhado
        # ANTES deste (quando `entitlement` não é o primeiro da cadeia) apareceria em `items` e
        # seria incorretamente empurrado pra depois dele sem este filtro.
        futuros = [item for item in items if item.id != entitlement.id and item.starts_at >= fim_antigo]
        for item, novo_inicio, novo_fim in chain.realign_after(futuros, entitlement.expires_at):
            item.starts_at = novo_inicio
            item.expires_at = novo_fim
        db.flush()
        db.commit()
        logger.info(
            "[ENTITLEMENT] estendido (indicação) user_id=%s id=%s +%s dias -> novo fim %s",
            user_id, entitlement.id, extra_days, entitlement.expires_at,
        )
        return entitlement
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
