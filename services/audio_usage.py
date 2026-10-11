"""Uso e limite da EXTRAÇÃO DE ÁUDIO (Parte A: avulsa -- autorização do CÉREBRO, 11/10/2026).

Regras de negócio (ver a autorização completa):
- Só plano PAGO ativo (Semanal, Mensal, VIP Batch) -- Free/deslogado é recusado aqui
  (AudioNotAllowedError, 403) -- nunca confiamos só no frontend escondendo/bloqueando a opção.
- NÃO consome geração: nunca cria/lê/conta nenhuma linha em `generations` -- ledger PRÓPRIO
  (`audio_extractions`, migration 0012, database/models/audio_extraction.py).
- Limite técnico: AUDIO_EXTRACTIONS_PER_DAY (config.py) por dia de São Paulo (utils/time_sp.py).
  `completed` sempre conta; `reserved` ainda não expirado (mesmo TTL de
  GENERATION_RESERVATION_TTL_SECONDS, reaproveitado -- nunca um segundo TTL inventado) também
  conta, para nunca permitir reservar mais do que o saldo enquanto uma extração ainda está em
  andamento; `failed` NUNCA conta (pedido explícito do CÉREBRO: falha de download/processamento e
  vídeo sem áudio não contam no limite diário).
- Uma operação de áudio por vez por usuário: enquanto existir qualquer AudioExtraction "reserved"
  (não expirada) deste usuário, uma nova reserva é recusada com AudioConcurrentOperationError
  (409) -- nunca duas extrações simultâneas do mesmo usuário.

A Parte B (lote) estende este módulo com reserve_audio_batch/run_audio_batch (ver o pedido do
CÉREBRO) -- este arquivo, nesta etapa, cobre só a extração AVULSA.
"""
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from config import AUDIO_EXTRACTIONS_PER_DAY, GENERATION_RESERVATION_TTL_SECONDS
from database.models.audio_extraction import AudioExtraction
from services.entitlements import get_current_entitlement
from services.locks import lock_user_row
from services.plans import FREE_PLAN, Plan, get_plan
from utils.time_sp import resolve_now, sp_day

logger = logging.getLogger("minhoca")

MAX_REQUEST_ID_LENGTH = 64
MAX_ERROR_CODE_LENGTH = 64


# ------------------------------------------------------------------------------ erros
class AudioUsageError(Exception):
    """Base de todos os erros desta camada -- cada subclasse carrega seu PRÓPRIO status_code/
    user_message (mesmo idioma de services/pinterest_board.py::PinterestBoardError), traduzidos
    por um ÚNICO handler em routes/audio.py (registrado para a classe BASE: Starlette despacha
    para a subclasse mais específica primeiro, mesmo padrão já usado em main.py para DownloadError/
    ProcessorError/ UsageError)."""

    status_code = 400
    user_message = "Não foi possível processar esta extração de áudio."

    def __init__(self, detail: str | None = None) -> None:
        self.detail = detail or self.user_message
        super().__init__(self.detail)


class AudioNotAllowedError(AudioUsageError):
    """Plano efetivo não é pago (Free, ou sem sessão) -- texto EXATO pedido pelo CÉREBRO."""

    status_code = 403
    user_message = "Extrair áudio é exclusivo dos planos pagos."


class AudioQuotaExceededError(AudioUsageError):
    """Limite diário (AUDIO_EXTRACTIONS_PER_DAY) esgotado -- texto EXATO pedido pelo CÉREBRO."""

    status_code = 429
    user_message = "Você usou suas 15 extrações de hoje. Amanhã libera de novo."

    def __init__(self, allowance: "AudioAllowance", requested: int) -> None:
        self.allowance = allowance
        self.requested = requested
        super().__init__(self.user_message)


class AudioConcurrentOperationError(AudioUsageError):
    """Já existe uma extração de áudio (avulsa ou de lote) "reserved", não expirada, deste
    usuário -- "uma operação de áudio por vez", pedido explícito do CÉREBRO."""

    status_code = 409
    user_message = "Já existe uma extração de áudio em andamento. Aguarde terminar."


class InvalidRequestIdError(AudioUsageError):
    status_code = 400


class AudioStateError(AudioUsageError):
    """Transição inválida (só reserved -> completed | failed) ou extração inexistente -- erro de
    infraestrutura, nunca esperado em uso normal."""

    status_code = 500


class AudioDownloadNotFoundError(Exception):
    """404 genérico -- mesma filosofia de services.usage.GenerationDownloadNotFoundError: nunca
    diferencia "não existe" de "não é seu" de "expirou", para não permitir enumerar extrações de
    outros usuários por tentativa e erro. Deliberadamente NÃO é AudioUsageError (handler HTTP
    próprio, 404, registrado em main.py)."""


# ------------------------------------------------------------------------------ resultados
@dataclass(frozen=True)
class AudioAllowance:
    plan: Plan
    limit: int
    used: int
    remaining: int
    period_day: date


@dataclass(frozen=True)
class AudioReservation:
    extraction_id: int
    request_id: str | None
    status: str
    period_day: date
    created: bool  # False = repetição idempotente de uma reserva que já existia


# ------------------------------------------------------------------------------ plano efetivo
def _utc(moment: datetime) -> datetime:
    return moment.astimezone(timezone.utc)


def _effective_plan(db: Session, user_id: int, now: datetime) -> Plan:
    entitlement = get_current_entitlement(db, user_id, now)  # levanta em caso de sobreposição
    return FREE_PLAN if entitlement is None else get_plan(entitlement.plan_code)


def _require_paid_plan(plan: Plan) -> None:
    if not plan.paid:
        raise AudioNotAllowedError()


# ------------------------------------------------------------------------------ cálculo do saldo
def _compute_allowance(db: Session, user_id: int, now: datetime, plan: Plan) -> AudioAllowance:
    """Plano já resolvido (reaproveitado de quem já chamou _effective_plan -- evita repetir a
    resolução de entitlement). Só leitura (quem reserva chama já com o lock)."""
    today = sp_day(now)
    stale_before = now - timedelta(seconds=GENERATION_RESERVATION_TTL_SECONDS)
    counts = or_(
        AudioExtraction.status == "completed",
        and_(AudioExtraction.status == "reserved", AudioExtraction.created_at > stale_before),
    )
    query = (
        select(func.count())
        .select_from(AudioExtraction)
        .where(AudioExtraction.user_id == user_id, AudioExtraction.period_day == today, counts)
    )
    used = db.execute(query).scalar_one()
    return AudioAllowance(
        plan=plan,
        limit=AUDIO_EXTRACTIONS_PER_DAY,
        used=used,
        remaining=max(0, AUDIO_EXTRACTIONS_PER_DAY - used),
        period_day=today,
    )


def get_audio_allowance(db: Session, user_id: int, now: datetime | None = None) -> AudioAllowance:
    """Saldo diário de extração de áudio (só leitura) -- usado pelo contador "X de 15 hoje" do
    frontend. Levanta AudioNotAllowedError (403) se o plano efetivo não for pago -- nunca calcula
    um saldo "de mentirinha" para o Free."""
    now = resolve_now(now)
    plan = _effective_plan(db, user_id, now)
    _require_paid_plan(plan)
    return _compute_allowance(db, user_id, now, plan)


def _has_active_reservation(db: Session, user_id: int, now: datetime) -> bool:
    """Existe alguma AudioExtraction "reserved" (não expirada pelo TTL) deste usuário agora? --
    "uma operação de áudio por vez" (avulsa OU de lote: todo item de lote também é uma
    AudioExtraction "reserved" enquanto seu processamento não termina)."""
    stale_before = now - timedelta(seconds=GENERATION_RESERVATION_TTL_SECONDS)
    return (
        db.execute(
            select(func.count())
            .select_from(AudioExtraction)
            .where(
                AudioExtraction.user_id == user_id,
                AudioExtraction.status == "reserved",
                AudioExtraction.created_at > stale_before,
            )
        ).scalar_one()
        > 0
    )


# ------------------------------------------------------------------------------ validações
def _validate_request_id(request_id) -> str:
    if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > MAX_REQUEST_ID_LENGTH:
        raise InvalidRequestIdError(f"request_id deve ser um texto de 1 a {MAX_REQUEST_ID_LENGTH} caracteres.")
    return request_id


# ------------------------------------------------------------------------------ varredura de reservas órfãs
def fail_stale_audio_reservations(db: Session, now: datetime | None = None) -> int:
    """Marca como failed as reservas de áudio mais velhas que o TTL (órfãs de um processamento que
    caiu) -- mesmo papel de services.usage.fail_stale_reservations, tabela própria. Devolve quantas
    foram marcadas."""
    now = resolve_now(now)
    stale_before = now - timedelta(seconds=GENERATION_RESERVATION_TTL_SECONDS)
    try:
        result = db.execute(
            update(AudioExtraction)
            .where(AudioExtraction.status == "reserved", AudioExtraction.created_at <= stale_before)
            .values(status="failed", error_code="reservation_expired", finished_at=_utc(now))
        )
        db.commit()
        return result.rowcount
    except Exception:
        db.rollback()
        raise


def _sweep_stale_best_effort(db: Session, now: datetime) -> None:
    """Roda fail_stale_audio_reservations no início de toda reserva nova -- mesmo padrão
    (best-effort, nunca bloqueia a reserva atual) de services.usage::_sweep_stale_reservations_best_effort."""
    try:
        marcadas = fail_stale_audio_reservations(db, now)
        if marcadas:
            logger.info("[AUDIO] %s reserva(s) de áudio órfã(s) marcada(s) como failed (TTL expirado)", marcadas)
    except Exception:
        db.rollback()
        logger.warning("[AUDIO] falha ao varrer reservas de áudio órfãs (não bloqueia a reserva atual)", exc_info=True)


# ------------------------------------------------------------------------------ reserva avulsa
def _to_reservation(extraction: AudioExtraction, *, created: bool) -> AudioReservation:
    return AudioReservation(
        extraction_id=extraction.id,
        request_id=extraction.request_id,
        status=extraction.status,
        period_day=extraction.period_day,
        created=created,
    )


def _find_by_request_id(db: Session, user_id: int, request_id: str) -> AudioExtraction | None:
    return db.execute(
        select(AudioExtraction)
        .where(AudioExtraction.user_id == user_id, AudioExtraction.request_id == request_id)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def reserve_audio_extraction(
    db: Session,
    *,
    user_id: int,
    request_id: str,
    platform: str | None = None,
    display_filename: str | None = None,
    now: datetime | None = None,
) -> AudioReservation:
    """Reserva UMA extração de áudio avulsa, ou levanta AudioNotAllowedError (plano não pago),
    AudioConcurrentOperationError (já há uma operação de áudio em andamento) ou
    AudioQuotaExceededError (15/dia esgotado). Idempotente por request_id -- repetir a mesma
    requisição devolve a MESMA reserva, sem consumir de novo (mesmo contrato de
    services.usage.reserve_generation)."""
    request_id = _validate_request_id(request_id)
    now = resolve_now(now)
    _sweep_stale_best_effort(db, now)
    try:
        lock_user_row(db, user_id)  # 1ª instrução: serializa as reservas deste usuário

        existing = _find_by_request_id(db, user_id, request_id)
        if existing is not None:  # repetição da mesma requisição: mesma reserva, sem novo consumo
            result = _to_reservation(existing, created=False)
            db.commit()
            return result

        plan = _effective_plan(db, user_id, now)
        _require_paid_plan(plan)

        if _has_active_reservation(db, user_id, now):
            raise AudioConcurrentOperationError()

        allowance = _compute_allowance(db, user_id, now, plan)
        if allowance.remaining < 1:
            raise AudioQuotaExceededError(allowance, requested=1)

        extraction = AudioExtraction(
            user_id=user_id,
            request_id=request_id,
            status="reserved",
            period_day=allowance.period_day,
            platform=platform,
            display_filename=display_filename,
            created_at=_utc(now),
        )
        db.add(extraction)
        db.flush()
        result = _to_reservation(extraction, created=True)
        db.commit()
        logger.info(
            "[AUDIO] reserva avulsa user_id=%s extração=%s restam=%s",
            user_id, result.extraction_id, allowance.remaining - 1,
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


# ------------------------------------------------------------------------------ transições do ledger
def complete_audio_extraction(
    db: Session,
    extraction_id: int,
    *,
    output_sha256: str | None = None,
    duration_ms: int | None = None,
    output_size_bytes: int | None = None,
    output_expires_at: datetime | None = None,
    output_storage_key: str | None = None,
    now: datetime | None = None,
) -> None:
    """reserved -> completed (continua contando). Não precisa do lock: nunca aumenta o consumo --
    mesmo raciocínio de services.usage.complete_generation."""
    now = resolve_now(now)
    try:
        result = db.execute(
            update(AudioExtraction)
            .where(AudioExtraction.id == extraction_id, AudioExtraction.status == "reserved")
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
            raise AudioStateError(f"A extração de áudio {extraction_id} não está reservada (ou não existe).")
        db.commit()
    except Exception:
        db.rollback()
        raise


def fail_audio_extraction(db: Session, extraction_id: int, *, error_code: str, now: datetime | None = None) -> None:
    """reserved -> failed (libera o limite diário -- "falhas não contam"). Não precisa do lock: só
    diminui o consumo."""
    if not isinstance(error_code, str) or not error_code.strip() or len(error_code) > MAX_ERROR_CODE_LENGTH:
        raise AudioStateError(f"error_code deve ter de 1 a {MAX_ERROR_CODE_LENGTH} caracteres.")
    now = resolve_now(now)
    try:
        result = db.execute(
            update(AudioExtraction)
            .where(AudioExtraction.id == extraction_id, AudioExtraction.status == "reserved")
            .values(status="failed", error_code=error_code, finished_at=_utc(now))
        )
        if result.rowcount != 1:
            raise AudioStateError(f"A extração de áudio {extraction_id} não está reservada (ou não existe).")
        db.commit()
    except Exception:
        db.rollback()
        raise


def get_downloadable_audio_extraction(
    db: Session, extraction_id: int, user_id: int, now: datetime | None = None
) -> AudioExtraction:
    """A ÚNICA fonte de verdade para "esta extração de áudio pode ser baixada por este usuário
    agora?" -- mesma filosofia (404 genérico) de services.usage.get_downloadable_generation."""
    now = resolve_now(now)
    extraction = db.get(AudioExtraction, extraction_id)
    if (
        extraction is None
        or extraction.user_id != user_id
        or extraction.status != "completed"
        or extraction.output_storage_key is None
        or extraction.output_expires_at is None
        or now >= extraction.output_expires_at
    ):
        raise AudioDownloadNotFoundError()
    return extraction
