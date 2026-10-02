"""Uso e consumo: "este usuário pode consumir uma geração agora?" e "reserve uma geração".

O ledger `generations` é a ÚNICA fonte do consumo (não existe contador). Regras:
- Free: 5 gerações por SEMANA de São Paulo (segunda a domingo), contando só gerações `free`.
- Pago: N gerações por DIA de São Paulo (Semanal 5, Mensal 10, VIP Batch 15 — ver
  services/plans.py, fonte única destes números), contando as gerações pagas do dia. O limite
  diário é COMPARTILHADO entre acessos empilhados no mesmo dia.
- Free e pago são isolados: o uso pago não gasta o saldo Free da semana e vice-versa.
- Contam para a cota: `completed` sempre; `reserved` (até GENERATION_RESERVATION_TTL_SECONDS).
  `failed` não conta. Uma reservada antiga (processamento que caiu) deixa de contar.
- Sem acesso vigente => Free. Com sobreposição de acessos => erro (nunca se cai no Free em silêncio).

Concorrência: reservar = lock do usuário (services/locks.py) + contar + inserir + commit, tudo numa
transação. Duas requisições para a última geração: uma reserva, a outra recebe QuotaExceededError.

Idempotência: pelo request_id (UNIQUE(user_id, request_id) da tabela generations; lote: em
batches). Repetir a mesma requisição devolve a MESMA reserva, sem consumir de novo. Para lote
(Etapa 9.3): repetir o mesmo request_id com o MESMO conteúdo (fingerprint, ver
services.batch_fingerprint) é o replay; com o mesmo tamanho mas conteúdo diferente é conflito
(BatchRequestConflictError) -- nunca reaproveita a reserva antiga silenciosamente.

Lote: qualquer plano pago com max_batch_size > 0 (Semanal, Mensal, VIP Batch; o Free não usa lote),
de 1 vídeo até o teto do plano, cada um consumindo 1 geração da MESMA cota (não há quota separada
de VÍDEOS para lote). A reserva é atômica (tudo ou nada): recusa o lote inteiro se faltar saldo,
nunca reduz o tamanho pedido para caber. Nada aqui baixa, processa ou gera arquivo.

Cota de OPERAÇÕES de lote (Etapa 9.2, dimensão independente da cota de vídeos acima): quantas
chamadas a reserve_batch (não quantos vídeos) um plano permite por semana comercial de São Paulo
(Plan.batch_limit/batch_period, services/plans.py). `Batch` já é o próprio ledger dessa cota —
não existe nenhuma tabela/coluna nova: contamos quantas linhas de `batches` este usuário já tem
com `created_at` dentro da semana atual (mesma convenção de semana usada no Free, sp_week_start),
sob o MESMO lock por usuário que já protege a cota de vídeos (services/locks.py) e na MESMA
transação em que o Batch é criado -- ou seja, "gastar" uma operação de lote é, literalmente,
criar a linha em `batches`; não há um contador separado para (des)sincronizar. Uma vez aceito, um
lote sempre conta como 1 operação usada, para sempre (nunca é devolvido por um item que falhou
depois -- só a geração individual que falhou libera SUA própria vaga de vídeo, nunca a operação de
lote em si). VIP Batch tem `batch_limit=None`: nenhuma contagem é feita (nunca levanta
BatchQuotaExceededError); o único teto que continua valendo para o VIP é o de vídeos/dia.
"""
import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from config import GENERATION_RESERVATION_TTL_SECONDS
from database.models import Batch, Generation
from services import result_storage
from services.entitlements import get_current_entitlement
from services.locks import UserNotFoundError, lock_user_row  # noqa: F401  (UserNotFoundError reexportado)
from services.plans import FREE, FREE_PLAN, Plan, get_plan
from utils.time_sp import SP, resolve_now, sp_day, sp_week_start

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
    """O plano efetivo não permite lote nenhum (hoje, só o Free cai aqui — Semanal, Mensal e VIP
    Batch têm max_batch_size > 0, ver services/plans.py)."""


class InvalidBatchSizeError(UsageError):
    pass


class BatchRequestConflictError(UsageError):
    """Mesmo request_id de lote reutilizado com um tamanho diferente."""


class BatchQuotaExceededError(UsageError):
    """Cota de OPERAÇÕES de lote esgotada na semana comercial (Etapa 9.2) -- dimensão
    INDEPENDENTE da cota de vídeos (QuotaExceededError). VIP Batch nunca levanta este erro
    (batch_limit=None, ver services/plans.py): para o VIP, só a cota de vídeos limita."""

    def __init__(self, batch_allowance: "BatchAllowance") -> None:
        self.batch_allowance = batch_allowance
        super().__init__(
            f"Cota de operações de lote esgotada: plano {batch_allowance.plan.code}, "
            f"{batch_allowance.used}/{batch_allowance.limit} na semana de {batch_allowance.period_start}."
        )


class InvalidRequestIdError(UsageError):
    pass


class GenerationStateError(UsageError):
    """Transição inválida (só reserved -> completed | failed) ou geração inexistente."""


class BatchNotFoundError(Exception):
    """Etapa 9.3: o lote não existe OU não pertence a este usuário -- mesma filosofia 404 genérica
    de GenerationDownloadNotFoundError (nunca diferencia os dois casos, para não permitir enumerar
    lotes de outros usuários por tentativa e erro). Deliberadamente NÃO é UsageError: tem handler
    HTTP próprio (404), para não cair no handler 400 de UsageError."""


class GenerationDownloadNotFoundError(Exception):
    """Etapa 8B.3: o resultado desta geração não está disponível para download — por QUALQUER
    motivo (inexistente, de outro usuário, ainda reserved/failed, sem storage_key, expirada, ou
    o arquivo físico sumiu). Deliberadamente NÃO é uma UsageError (não é uma regra de cota) — tem
    handler HTTP próprio (404 genérico, routes/generations.py), para nunca cair no handler 400 de
    UsageError. A mensagem é sempre genérica de propósito: nunca revela qual das condições falhou,
    para não permitir enumerar gerações de outros usuários por tentativa e erro."""


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


@dataclass(frozen=True)
class BatchAllowance:
    """Cota de OPERAÇÕES de lote (não de vídeos) do usuário agora -- Etapa 9.2. `limit`/`remaining`
    são `None` quando o plano não conta (VIP Batch: ilimitado). Free (batch_limit=0) aparece aqui
    com limit=0/remaining=0/used=0, sem nenhuma consulta ao banco (não tem lote, ponto final)."""

    plan: Plan
    limit: int | None
    used: int
    remaining: int | None
    period_start: date  # segunda-feira (SP) da semana usada para contar

    @property
    def unlimited(self) -> bool:
        return self.plan.batch_enabled and self.limit is None


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


# ------------------------------------------------------------------------------ cota de OPERAÇÕES de lote (Etapa 9.2)
def _week_bounds_utc(now: datetime) -> tuple[datetime, datetime]:
    """[início, fim) da semana comercial de São Paulo de `now` (segunda 00:00 -> próxima segunda
    00:00), convertido para UTC -- para comparar contra Batch.created_at (guardado em UTC) com um
    intervalo simples. Reaproveita sp_week_start (MESMA convenção de semana já usada pelo Free);
    não inventa uma segunda definição de semana. `Batch` não tem period_week próprio (ao
    contrário de Generation): uma operação de lote não muda de "semana" depois de criada, então um
    intervalo sobre created_at já é exato e suficiente -- não precisamos materializar o período
    numa coluna nova só para isso."""
    monday = sp_week_start(now)
    start_sp = datetime.combine(monday, time.min, tzinfo=SP)
    return _utc(start_sp), _utc(start_sp + timedelta(days=7))


def _count_batches_in_week(db: Session, user_id: int, now: datetime) -> int:
    """Quantas OPERAÇÕES de lote (linhas de `batches`, não vídeos) este usuário já tem na semana
    comercial de `now`. `batches` é o próprio ledger: nenhum contador/tabela separada existe ou é
    necessário. Nunca filtra por status (Batch não tem coluna de status, e uma operação aceita
    conta para sempre, mesmo que um item dela falhe depois -- ver BatchQuotaExceededError)."""
    start_utc, end_utc = _week_bounds_utc(now)
    return db.execute(
        select(func.count())
        .select_from(Batch)
        .where(Batch.user_id == user_id, Batch.created_at >= start_utc, Batch.created_at < end_utc)
    ).scalar_one()


def _compute_batch_allowance(db: Session, user_id: int, plan: Plan, now: datetime) -> BatchAllowance:
    """Cota de operações de lote do plano EFETIVO já resolvido (reaproveitado de quem já chamou
    _compute_allowance -- evita repetir a resolução de entitlement). Só leitura."""
    period_start = sp_week_start(now)
    if not plan.batch_enabled:  # Free (ou qualquer plano futuro sem lote): nada a contar
        return BatchAllowance(plan=plan, limit=0, used=0, remaining=0, period_start=period_start)
    if plan.batch_limit is None:  # VIP Batch: ilimitado -- nenhuma consulta ao banco é necessária
        return BatchAllowance(plan=plan, limit=None, used=0, remaining=None, period_start=period_start)
    used = _count_batches_in_week(db, user_id, now)
    return BatchAllowance(
        plan=plan, limit=plan.batch_limit, used=used,
        remaining=max(0, plan.batch_limit - used), period_start=period_start,
    )


def get_batch_allowance(db: Session, user_id: int, now: datetime | None = None) -> BatchAllowance:
    """Cota de OPERAÇÕES de lote do usuário agora (só leitura) -- dimensão independente da cota de
    vídeos (get_allowance). VIP Batch: limit/remaining sempre None (ilimitado); Free: sempre 0."""
    now = resolve_now(now)
    entitlement = get_current_entitlement(db, user_id, now)
    plan = FREE_PLAN if entitlement is None else get_plan(entitlement.plan_code)
    return _compute_batch_allowance(db, user_id, plan, now)


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
    display_filename: str | None = None,
    now: datetime | None = None,
) -> Reservation:
    """Reserva UMA geração para o usuário ou levanta QuotaExceededError. Idempotente por request_id.

    `display_filename` (geração avulsa ganhando nome de arquivo opcional, 02/10/2026): já vem
    SANITIZADO por quem chama (services.generation_flow, via services.batch_filenames) -- nada
    aqui valida ou normaliza. Mesmo campo que o item de lote já usa (Generation.display_filename,
    migration 0004) -- o download individual (routes/generations.py) já lê esse campo há tempo,
    então nenhuma rota de download precisa mudar."""
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
            display_filename=display_filename,
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
    display_filename: str | None = None,
) -> Generation:
    """Uma geração de item de lote (função separada para poder simular falhas nos testes).
    `display_filename` (Etapa 9.3, correção de filename): já vem SANITIZADO por quem chama
    (routes/generations.py, via services.batch_filenames) -- nada aqui valida ou normaliza."""
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
        display_filename=display_filename,
        created_at=_utc(now),
    )


def _batch_replay(
    db: Session, batch: Batch, size: int, content_fingerprint: str | None = None
) -> BatchReservation:
    """Repetição idempotente de um request_id de lote. `content_fingerprint` (Etapa 9.3, correção
    de idempotência): quando o CHAMADOR fornece um fingerprint (rota nova) E o Batch já tinha um
    gravado (lote criado depois desta etapa), os dois precisam bater -- mesmo request_id, mesma
    quantidade, mas URLs/filenames diferentes é CONFLITO, não um replay. Quando qualquer um dos
    dois lados é None (chamador não forneceu, ou o Batch é anterior a esta coluna existir --
    compatibilidade, ver migration 0005), a comparação é pulada (nunca um falso conflito)."""
    if batch.item_count != size:
        raise BatchRequestConflictError("Este request_id de lote já foi usado com outro tamanho.")
    if (
        content_fingerprint is not None
        and batch.request_fingerprint is not None
        and batch.request_fingerprint != content_fingerprint
    ):
        raise BatchRequestConflictError("Este request_id de lote já foi usado com um conteúdo diferente.")
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
    content_fingerprint: str | None = None,
    display_filenames: list[str | None] | None = None,
) -> BatchReservation:
    """Reserva um lote de `size` vídeos (1 geração cada), TUDO OU NADA. Qualquer plano pago com
    max_batch_size > 0 usa lote (hoje: Semanal, Mensal e VIP Batch; o Free não usa lote) — ver
    services/plans.py, fonte única de qual plano tem lote habilitado e do teto por operação.

    Etapa 9.2: além da cota de VÍDEOS (a mesma cota da geração avulsa), toda chamada aceita
    também consome 1 OPERAÇÃO de lote da cota semanal do plano (`Plan.batch_limit`) -- as duas
    cotas são independentes (ver módulo) e as duas precisam ter saldo para o lote ser aceito;
    faltando qualquer uma delas, nada é gravado. VIP Batch nunca é limitado por operações
    (`batch_limit=None`); Free nunca chega a essa checagem (recusado antes, por não ter lote).

    Sem saldo (de vídeos OU de operações) para o lote inteiro: nada é gravado (nem reserva
    parcial). Idempotente por request_id -- uma repetição nunca consome uma nova operação.

    Etapa 9.3 (correção de idempotência real): `content_fingerprint`, quando fornecido (ver
    services.batch_fingerprint), é gravado no Batch e comparado numa repetição do MESMO
    request_id -- mesmo tamanho mas conteúdo (URLs/filenames/ordem) diferente vira
    BatchRequestConflictError, nunca um replay silencioso. Parâmetro OPCIONAL (default None,
    compatibilidade retroativa total com todo chamador que não o fornecer -- nenhum teste
    existente desta função precisou mudar).

    Etapa 9.3 (correção de filename): `display_filenames`, quando fornecido, deve ter exatamente
    `size` posições (uma por item, na mesma ordem) -- cada uma já SANITIZADA por quem chama
    (services.batch_filenames), gravada em Generation.display_filename por posição. Nada aqui
    sanitiza ou normaliza; um valor com formato inesperado é responsabilidade do chamador.
    """
    request_id = _validate_request_id(request_id)
    size = _validate_batch_size(size)
    if display_filenames is not None and len(display_filenames) != size:
        raise InvalidBatchSizeError("display_filenames deve ter exatamente `size` posições.")
    now = resolve_now(now)
    try:
        lock_user_row(db, user_id)

        existing = db.execute(
            select(Batch).where(Batch.user_id == user_id, Batch.request_id == request_id)
        ).scalar_one_or_none()
        if existing is not None:
            result = _batch_replay(db, existing, size, content_fingerprint)
            db.commit()
            return result

        allowance = _compute_allowance(db, user_id, now)
        if not allowance.plan.batch_enabled:
            raise BatchNotAllowedError(f"O plano {allowance.plan.code} não permite processamento em lote.")
        if size > allowance.plan.max_batch_size:
            raise InvalidBatchSizeError(f"O lote pode ter no máximo {allowance.plan.max_batch_size} vídeos.")

        # Cota de OPERAÇÕES de lote (independente da cota de vídeos abaixo): ainda dentro do MESMO
        # lock_user_row/transação, então nenhuma outra chamada consegue "ver" a mesma vaga livre
        # (o UPDATE de lock_user_row já serializou este usuário -- mesmo mecanismo que protege a
        # cota de vídeos, nenhum novo lock foi criado). batch_limit=None (VIP) nunca cai aqui.
        batch_allowance = _compute_batch_allowance(db, user_id, allowance.plan, now)
        if batch_allowance.limit is not None and batch_allowance.remaining < 1:
            raise BatchQuotaExceededError(batch_allowance)

        if allowance.remaining < size:
            raise QuotaExceededError(allowance, requested=size)

        batch = Batch(
            user_id=user_id, request_id=request_id, item_count=size,
            request_fingerprint=content_fingerprint, created_at=_utc(now),
        )
        db.add(batch)
        db.flush()
        generations = [
            _new_generation(
                user_id=user_id, batch_id=batch.id, position=position,
                allowance=allowance, platform=platform, now=now,
                display_filename=None if display_filenames is None else display_filenames[position - 1],
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
        result = _batch_replay(db, existing, size, content_fingerprint)
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
    output_size_bytes: int | None = None,
    output_expires_at: datetime | None = None,
    output_storage_key: str | None = None,
    now: datetime | None = None,
) -> None:
    """reserved -> completed (continua contando). Não precisa do lock: nunca aumenta o consumo.

    output_size_bytes/output_expires_at/output_storage_key (Etapa 8B.1/8B.2) descrevem o
    resultado disponível para download, quando existir — todos opcionais/None por padrão, sem
    nenhuma mudança na regra de cota/reserva. Quem calcula `output_expires_at` (finished_at + TTL)
    é o chamador (services/generation_flow.py), a partir do MESMO `now` passado aqui, para os dois
    valores baterem exatamente."""
    now = resolve_now(now)
    try:
        result = db.execute(
            update(Generation)
            .where(Generation.id == generation_id, Generation.status == "reserved")
            .values(
                status="completed",
                finished_at=_utc(now),
                output_sha256=output_sha256,
                duration_ms=duration_ms,
                output_size_bytes=output_size_bytes,
                output_expires_at=output_expires_at,
                output_storage_key=output_storage_key,
            )
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


def get_downloadable_generation(
    db: Session, generation_id: int, user_id: int, *,
    authenticated_user_id: int | None = None, now: datetime | None = None,
) -> Generation:
    """Etapa 8B.3: a ÚNICA fonte de verdade para "esta geração pode ser baixada por este usuário
    agora?" — usada exclusivamente pela rota de download (routes/generations.py), para não
    duplicar esta checagem em nenhum outro lugar. Levanta GenerationDownloadNotFoundError (a
    MESMA exceção, com a MESMA mensagem genérica) para qualquer uma destas condições:
    - a geração não existe;
    - não pertence nem a `user_id` nem a `authenticated_user_id` (nunca um erro diferente do "não
      existe" — não é permitido diferenciar os casos, para não virar um jeito de descobrir
      gerações de terceiros);
    - o status não é "completed" (ainda reserved, ou failed);
    - output_storage_key é None (nunca teve um resultado persistido, ou é uma geração antiga de
      antes da Etapa 8B.1/8B.2);
    - output_expires_at é None, ou já passou (now >= output_expires_at).
    NÃO confere se o arquivo físico ainda existe — isso é responsabilidade de quem chama (a rota),
    via services.result_storage.exists(), depois de ler a chave desta geração.

    `authenticated_user_id` (opcional -- correção da quota Free, aprovação do CÉREBRO): desde que
    a identidade EFETIVA de uma sessão SEM plano pago (`user_id`, vindo de
    routes/deps.py::get_generation_user) passou a ser a do dispositivo, uma geração antiga
    pertencente à própria CONTA autenticada (criada antes desta correção, ou enquanto o plano
    ainda era pago) precisa continuar baixável por ela. `None` (padrão) preserva exatamente o
    comportamento anterior -- só `user_id` é considerado dono. O cookie Free em si, sozinho,
    continua incapaz de baixar uma geração de uma conta alheia, e continua incapaz de conceder
    acesso pago -- `authenticated_user_id` só entra na decisão quando HÁ mesmo uma sessão válida
    (nunca inventado a partir do cookie anônimo)."""
    now = resolve_now(now)
    generation = db.get(Generation, generation_id)
    donos_validos = {user_id, authenticated_user_id}
    if (
        generation is None
        or generation.user_id not in donos_validos
        or generation.status != "completed"
        or generation.output_storage_key is None
        or generation.output_expires_at is None
        or now >= generation.output_expires_at
    ):
        raise GenerationDownloadNotFoundError()
    return generation


def get_owned_batch(db: Session, batch_id: int, user_id: int) -> Batch:
    """Etapa 9.3: a ÚNICA fonte de verdade para "este lote pode ser acessado (ZIP, etc.) por este
    usuário?" -- usada pela rota de download do ZIP (routes/generations.py), para não duplicar
    esta checagem. Levanta BatchNotFoundError (mesma exceção, mesma mensagem genérica) tanto para
    "não existe" quanto para "é de outro usuário" -- nunca diferencia os dois casos."""
    batch = db.get(Batch, batch_id)
    if batch is None or batch.user_id != user_id:
        raise BatchNotFoundError()
    return batch


def get_downloadable_batch_generations(
    db: Session, batch_id: int, user_id: int, now: datetime | None = None
) -> list[Generation]:
    """Correção arquitetural pós-Etapa 9.3: a ÚNICA fonte de verdade para "quais itens deste lote
    podem entrar no ZIP de download, para este usuário, agora?" -- usada exclusivamente pela rota
    do ZIP (routes/generations.py), que antes decidia isso sozinha (consultava Generation, filtrava
    status, comparava output_expires_at, chamava result_storage.exists()) duplicando a mesma
    responsabilidade que get_downloadable_generation já centraliza para a geração avulsa.

    Reaproveita get_owned_batch (ownership do Batch -- mesma BatchNotFoundError, mesma mensagem
    genérica, nunca reimplementada aqui) e aplica os MESMOS critérios de elegibilidade que
    get_downloadable_generation já usa: status "completed", output_storage_key presente,
    output_expires_at presente e ainda não vencido, e o arquivo físico ainda existir no
    result_storage. Levanta BatchNotFoundError (a MESMA exceção do lote inexistente/de outro
    usuário) quando NENHUM item é elegível -- mesma filosofia de 404 genérico: nunca diferencia
    "lote não existe" de "lote existe mas está vazio/expirado/tudo falhou". Devolve a lista já
    ORDENADA por position -- a rota só monta o ZIP a partir dela, sem nenhuma decisão própria."""
    batch = get_owned_batch(db, batch_id, user_id)
    now = resolve_now(now)
    generations = db.execute(
        select(Generation)
        .where(Generation.batch_id == batch.id, Generation.status == "completed")
        .order_by(Generation.position)
    ).scalars().all()
    disponiveis = [
        g for g in generations
        if g.output_storage_key
        and g.output_expires_at is not None
        and now < g.output_expires_at
        and result_storage.exists(g.output_storage_key)
    ]
    if not disponiveis:
        raise BatchNotFoundError()
    return disponiveis


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
