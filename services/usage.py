"""Uso e consumo: "este usuário pode consumir uma geração agora?" e "reserve uma geração".

O ledger `generations` é a ÚNICA fonte do consumo (não existe contador). Regras:
- Free: 5 gerações por SEMANA de São Paulo (segunda a domingo), contando só gerações `free`.
- Pago: N gerações por DIA de São Paulo (Semanal 10, Mensal 20, VIP 30), contando as gerações
  pagas do dia. O limite diário é COMPARTILHADO entre acessos empilhados no mesmo dia.
- Free e pago são isolados: o uso pago não gasta o saldo Free da semana e vice-versa.
- Contam para a cota: `completed` sempre; `reserved` (até GENERATION_RESERVATION_TTL_SECONDS).
  `failed` não conta. Uma reservada antiga (processamento que caiu) deixa de contar.
- Sem acesso vigente => Free. Com sobreposição de acessos => erro (nunca se cai no Free em silêncio).

Concorrência: reservar = lock do usuário (services/locks.py) + contar + inserir + commit, tudo numa
transação. Duas requisições para a última geração: uma reserva, a outra recebe QuotaExceededError.

Idempotência: pelo request_id (UNIQUE(user_id, request_id) da tabela generations; lote: em
batches). Repetir a mesma requisição devolve a MESMA reserva, sem consumir de novo.

Lote: qualquer plano pago com max_batch_size > 0 (Semanal, Mensal, VIP Batch; o Free não usa lote),
de 1 vídeo até o teto do plano, cada um consumindo 1 geração da MESMA cota (não há quota separada
para lote). A reserva é atômica (tudo ou nada): recusa o lote inteiro se faltar saldo, nunca reduz
o tamanho pedido para caber. Nada aqui baixa, processa ou gera arquivo.
"""
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from config import GENERATION_RESERVATION_TTL_SECONDS
from database.models import Batch, Generation
from services.entitlements import get_current_entitlement
from services.locks import UserNotFoundError, lock_user_row  # noqa: F401  (UserNotFoundError reexportado)
from services.plans import FREE, FREE_PLAN, Plan, get_plan
from utils.time_sp import resolve_now, sp_day, sp_week_start

logger = logging.getLogger("minhoca")

MAX_REQUEST_ID_LENGTH = 64
MAX_ERROR_CODE_LENGTH = 64


# ------------------------------------------------------------------------------ erros
class UsageError(Exception):
    """Erro de regra de uso."""


class QuotaExceededError(UsageError):
    def __init__(self, allowance: "Allowance", requested: int) -> None:
        self.allowance = allowance
        self.requested = requested
        super().__init__(
            f"Cota esgotada: plano {allowance.plan.code}, {allowance.used}/{allowance.limit} "
            f"por {allowance.plan.period}, pedido {requested}, restam {allowance.remaining}."
        )


class BatchNotAllowedError(UsageError):
    """O plano efetivo não permite lote (só o VIP)."""


class InvalidBatchSizeError(UsageError):
    pass


class BatchRequestConflictError(UsageError):
    """Mesmo request_id de lote reutilizado com um tamanho diferente."""


class InvalidRequestIdError(UsageError):
    pass


class GenerationStateError(UsageError):
    """Transição inválida (só reserved -> completed | failed) ou geração inexistente."""


# ------------------------------------------------------------------------------ resultados
@dataclass(frozen=True)
class Allowance:
    plan: Plan
    entitlement_id: int | None
    limit: int
    used: int
    remaining: int
    period_day: date
    period_week: date

    @property
    def can_batch(self) -> bool:
        return self.plan.batch_enabled


@dataclass(frozen=True)
class Reservation:
    generation_id: int
    request_id: str | None
    status: str
    plan_code: str
    period_day: date
    period_week: date
    created: bool  # False = repetição idempotente de uma reserva que já existia


@dataclass(frozen=True)
class BatchReservation:
    batch_id: int
    request_id: str
    item_count: int
    generation_ids: tuple[int, ...]
    plan_code: str
    created: bool


# ------------------------------------------------------------------------------ cálculo do saldo
def _utc(moment: datetime) -> datetime:
    return moment.astimezone(timezone.utc)


def _compute_allowance(db: Session, user_id: int, now: datetime) -> Allowance:
    """Plano efetivo, limite, consumo e saldo. Só leitura (quem reserva chama já com o lock)."""
    entitlement = get_current_entitlement(db, user_id, now)  # levanta em caso de sobreposição
    plan = FREE_PLAN if entitlement is None else get_plan(entitlement.plan_code)
    today, week = sp_day(now), sp_week_start(now)
    stale_before = now - timedelta(seconds=GENERATION_RESERVATION_TTL_SECONDS)

    counts = or_(
        Generation.status == "completed",
        and_(Generation.status == "reserved", Generation.created_at > stale_before),
    )
    query = select(func.count()).select_from(Generation).where(Generation.user_id == user_id, counts)
    if plan.period == "week":
        query = query.where(Generation.plan_code == FREE, Generation.period_week == week)
    else:
        query = query.where(Generation.plan_code != FREE, Generation.period_day == today)
    used = db.execute(query).scalar_one()
    return Allowance(
        plan=plan,
        entitlement_id=None if entitlement is None else entitlement.id,
        limit=plan.limit,
        used=used,
        remaining=max(0, plan.limit - used),
        period_day=today,
        period_week=week,
    )


def get_allowance(db: Session, user_id: int, now: datetime | None = None) -> Allowance:
    """Plano efetivo e saldo do usuário agora (só leitura)."""
    return _compute_allowance(db, user_id, resolve_now(now))


def can_consume(db: Session, user_id: int, *, count: int = 1, now: datetime | None = None) -> bool:
    """Este usuário pode consumir `count` gerações agora?"""
    return get_allowance(db, user_id, now).remaining >= count


# ------------------------------------------------------------------------------ validações
def _validate_request_id(request_id) -> str:
    if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > MAX_REQUEST_ID_LENGTH:
        raise InvalidRequestIdError(f"request_id deve ser um texto de 1 a {MAX_REQUEST_ID_LENGTH} caracteres.")
    return request_id


def _validate_batch_size(size) -> int:
    if isinstance(size, bool) or not isinstance(size, int) or size < 1:
        raise InvalidBatchSizeError("O lote deve ter pelo menos 1 vídeo.")
    return size


# ------------------------------------------------------------------------------ reserva avulsa
def _to_reservation(generation: Generation, *, created: bool) -> Reservation:
    return Reservation(
        generation_id=generation.id,
        request_id=generation.request_id,
        status=generation.status,
        plan_code=generation.plan_code,
        period_day=generation.period_day,
        period_week=generation.period_week,
        created=created,
    )


def _find_by_request_id(db: Session, user_id: int, request_id: str) -> Generation | None:
    return db.execute(
        select(Generation)
        .where(Generation.user_id == user_id, Generation.request_id == request_id)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def reserve_generation(
    db: Session,
    *,
    user_id: int,
    request_id: str,
    platform: str | None = None,
    now: datetime | None = None,
) -> Reservation:
    """Reserva UMA geração para o usuário ou levanta QuotaExceededError. Idempotente por request_id."""
    request_id = _validate_request_id(request_id)
    now = resolve_now(now)
    try:
        lock_user_row(db, user_id)  # 1ª instrução: serializa as reservas deste usuário

        existing = _find_by_request_id(db, user_id, request_id)
        if existing is not None:  # repetição da mesma requisição: mesma reserva, sem novo consumo
            result = _to_reservation(existing, created=False)
            db.commit()
            return result

        allowance = _compute_allowance(db, user_id, now)
        if allowance.remaining < 1:
            raise QuotaExceededError(allowance, requested=1)

        generation = Generation(
            user_id=user_id,
            request_id=request_id,
            plan_code=allowance.plan.code,
            entitlement_id=allowance.entitlement_id,
            status="reserved",
            period_day=allowance.period_day,
            period_week=allowance.period_week,
            platform=platform,
            created_at=_utc(now),
        )
        db.add(generation)
        db.flush()
        result = _to_reservation(generation, created=True)
        db.commit()
        logger.info(
            "[USAGE] reserva user_id=%s geração=%s plano=%s restam=%s",
            user_id, result.generation_id, allowance.plan.code, allowance.remaining - 1,
        )
        return result
    except IntegrityError:
        # Só ocorreria se o mesmo request_id fosse inserido por outra transação sem passar pelo lock.
        db.rollback()
        existing = _find_by_request_id(db, user_id, request_id)
        if existing is None:
            raise
        result = _to_reservation(existing, created=False)
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise


# ------------------------------------------------------------------------------ reserva de lote
def _new_generation(
    *,
    user_id: int,
    batch_id: int,
    position: int,
    allowance: Allowance,
    platform: str | None,
    now: datetime,
) -> Generation:
    """Uma geração de item de lote (função separada para poder simular falhas nos testes)."""
    return Generation(
        user_id=user_id,
        batch_id=batch_id,
        position=position,
        request_id=None,
        plan_code=allowance.plan.code,
        entitlement_id=allowance.entitlement_id,
        status="reserved",
        period_day=allowance.period_day,
        period_week=allowance.period_week,
        platform=platform,
        created_at=_utc(now),
    )


def _batch_replay(db: Session, batch: Batch, size: int) -> BatchReservation:
    if batch.item_count != size:
        raise BatchRequestConflictError("Este request_id de lote já foi usado com outro tamanho.")
    rows = db.execute(
        select(Generation.id, Generation.plan_code)
        .where(Generation.batch_id == batch.id)
        .order_by(Generation.position)
    ).all()
    return BatchReservation(
        batch_id=batch.id,
        request_id=batch.request_id,
        item_count=batch.item_count,
        generation_ids=tuple(row.id for row in rows),
        plan_code=rows[0].plan_code if rows else "",
        created=False,
    )


def reserve_batch(
    db: Session,
    *,
    user_id: int,
    request_id: str,
    size: int,
    platform: str | None = None,
    now: datetime | None = None,
) -> BatchReservation:
    """Reserva um lote de `size` vídeos (1 geração cada), TUDO OU NADA. Só o VIP usa lote.

    Sem saldo para o lote inteiro: nada é gravado (nem reserva parcial). Idempotente por request_id.
    """
    request_id = _validate_request_id(request_id)
    size = _validate_batch_size(size)
    now = resolve_now(now)
    try:
        lock_user_row(db, user_id)

        existing = db.execute(
            select(Batch).where(Batch.user_id == user_id, Batch.request_id == request_id)
        ).scalar_one_or_none()
        if existing is not None:
            result = _batch_replay(db, existing, size)
            db.commit()
            return result

        allowance = _compute_allowance(db, user_id, now)
        if not allowance.plan.batch_enabled:
            raise BatchNotAllowedError(f"O plano {allowance.plan.code} não permite processamento em lote.")
        if size > allowance.plan.max_batch_size:
            raise InvalidBatchSizeError(f"O lote pode ter no máximo {allowance.plan.max_batch_size} vídeos.")
        if allowance.remaining < size:
            raise QuotaExceededError(allowance, requested=size)

        batch = Batch(user_id=user_id, request_id=request_id, item_count=size, created_at=_utc(now))
        db.add(batch)
        db.flush()
        generations = [
            _new_generation(
                user_id=user_id, batch_id=batch.id, position=position,
                allowance=allowance, platform=platform, now=now,
            )
            for position in range(1, size + 1)
        ]
        db.add_all(generations)
        db.flush()
        result = BatchReservation(
            batch_id=batch.id,
            request_id=request_id,
            item_count=size,
            generation_ids=tuple(g.id for g in generations),
            plan_code=allowance.plan.code,
            created=True,
        )
        db.commit()
        logger.info("[USAGE] lote user_id=%s batch=%s itens=%s restam=%s", user_id, batch.id, size, allowance.remaining - size)
        return result
    except IntegrityError:
        db.rollback()
        existing = db.execute(
            select(Batch).where(Batch.user_id == user_id, Batch.request_id == request_id)
        ).scalar_one_or_none()
        if existing is None:
            raise
        result = _batch_replay(db, existing, size)
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise


# ------------------------------------------------------------------------------ transições do ledger
def complete_generation(
    db: Session,
    generation_id: int,
    *,
    output_sha256: str | None = None,
    duration_ms: int | None = None,
    now: datetime | None = None,
) -> None:
    """reserved -> completed (continua contando). Não precisa do lock: nunca aumenta o consumo."""
    now = resolve_now(now)
    try:
        result = db.execute(
            update(Generation)
            .where(Generation.id == generation_id, Generation.status == "reserved")
            .values(status="completed", finished_at=_utc(now), output_sha256=output_sha256, duration_ms=duration_ms)
        )
        if result.rowcount != 1:
            raise GenerationStateError(f"A geração {generation_id} não está reservada (ou não existe).")
        db.commit()
    except Exception:
        db.rollback()
        raise


def fail_generation(
    db: Session, generation_id: int, *, error_code: str, now: datetime | None = None
) -> None:
    """reserved -> failed (libera a cota). Não precisa do lock: só diminui o consumo."""
    if not isinstance(error_code, str) or not error_code.strip() or len(error_code) > MAX_ERROR_CODE_LENGTH:
        raise GenerationStateError(f"error_code deve ter de 1 a {MAX_ERROR_CODE_LENGTH} caracteres.")
    now = resolve_now(now)
    try:
        result = db.execute(
            update(Generation)
            .where(Generation.id == generation_id, Generation.status == "reserved")
            .values(status="failed", error_code=error_code, finished_at=_utc(now))
        )
        if result.rowcount != 1:
            raise GenerationStateError(f"A geração {generation_id} não está reservada (ou não existe).")
        db.commit()
    except Exception:
        db.rollback()
        raise


def fail_stale_reservations(db: Session, now: datetime | None = None) -> int:
    """Marca como failed as reservas mais velhas que o TTL (orfãs de um processamento que caiu).
    Devolve quantas foram marcadas.
    Ainda não é chamada por nada: a etapa de processamento decide quando."""
    now = resolve_now(now)
    stale_before = now - timedelta(seconds=GENERATION_RESERVATION_TTL_SECONDS)
    try:
        result = db.execute(
            update(Generation)
            .where(Generation.status == "reserved", Generation.created_at <= stale_before)
            .values(status="failed", error_code="reservation_expired", finished_at=_utc(now))
        )
        db.commit()
        return result.rowcount
    except Exception:
        db.rollback()
        raise
