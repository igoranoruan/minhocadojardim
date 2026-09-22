"""Reserva de geração: saldo, limite, ledger, transições, idempotência e validações."""
from datetime import date

import pytest
from sqlalchemy import select

from config import GENERATION_RESERVATION_TTL_SECONDS
from database.models import Generation
from helpers_usage import DIA, NOW, consume, give, raw_entitlement
from services.entitlements import EntitlementInconsistencyError
from services.locks import UserNotFoundError
from services.usage import (
    GenerationStateError,
    InvalidRequestIdError,
    QuotaExceededError,
    can_consume,
    complete_generation,
    fail_generation,
    fail_stale_reservations,
    get_allowance,
    reserve_generation,
)


def _linhas(session):
    session.expire_all()
    return list(session.execute(select(Generation).order_by(Generation.id)).scalars())


def _reservar(session, usuario, request_id, **kw):
    return reserve_generation(session, user_id=usuario.id, request_id=request_id, now=kw.pop("now", NOW), **kw)


# ============================================================================ reserva e saldo
def test_reserva_free_decrementa_o_saldo_e_grava_no_ledger(factory, session):
    usuario = factory.user()
    reserva = _reservar(session, usuario, "req-1", platform="tiktok")
    assert reserva.created and reserva.status == "reserved" and reserva.plan_code == "free"
    assert get_allowance(session, usuario.id, now=NOW).remaining == 4

    (linha,) = _linhas(session)
    assert linha.id == reserva.generation_id and linha.user_id == usuario.id
    assert (linha.status, linha.plan_code, linha.entitlement_id) == ("reserved", "free", None)
    assert (linha.period_day, linha.period_week) == (date(2026, 9, 23), date(2026, 9, 21))
    assert linha.request_id == "req-1" and linha.batch_id is None and linha.position is None
    assert linha.platform == "tiktok" and linha.created_at == NOW and linha.output_sha256 is None


def test_reserva_paga_registra_o_plano_e_o_entitlement(factory, session):
    usuario = factory.user()
    acesso = give(factory, session, usuario, "weekly")
    reserva = _reservar(session, usuario, "req-1")
    assert reserva.plan_code == "weekly"
    (linha,) = _linhas(session)
    assert (linha.plan_code, linha.entitlement_id) == ("weekly", acesso.id)
    assert get_allowance(session, usuario.id, now=NOW).remaining == 4


def test_o_limite_nao_pode_ser_ultrapassado_free(factory, session):
    usuario = factory.user()
    for i in range(5):
        _reservar(session, usuario, f"r{i}")
    with pytest.raises(QuotaExceededError) as erro:
        _reservar(session, usuario, "r5")
    a = erro.value.allowance
    assert (a.plan.code, a.limit, a.used, a.remaining, erro.value.requested) == ("free", 5, 5, 0, 1)
    assert len(_linhas(session)) == 5  # a recusada não deixou nada no ledger


@pytest.mark.parametrize("plano, limite", [("weekly", 5), ("monthly", 10), ("vip_batch", 15)])
def test_o_limite_nao_pode_ser_ultrapassado_pagos(factory, session, plano, limite):
    usuario = factory.user()
    give(factory, session, usuario, plano)
    consume(session, usuario, limite, now=NOW)
    with pytest.raises(QuotaExceededError) as erro:
        _reservar(session, usuario, "a-mais")
    assert erro.value.allowance.limit == limite and erro.value.allowance.remaining == 0
    assert len(_linhas(session)) == limite


def test_concluida_continua_contando(factory, session):
    usuario = factory.user()
    consume(session, usuario, 5, now=NOW, complete=True)
    assert get_allowance(session, usuario.id, now=NOW).remaining == 0
    with pytest.raises(QuotaExceededError):
        _reservar(session, usuario, "nova")


def test_falhada_nao_consome_definitivamente(factory, session):
    usuario = factory.user()
    ids = consume(session, usuario, 5, now=NOW, complete=False)  # 5 reservadas
    with pytest.raises(QuotaExceededError):
        _reservar(session, usuario, "cheia")
    fail_generation(session, ids[0], error_code="download_failed", now=NOW)  # uma falha libera UMA vaga
    assert get_allowance(session, usuario.id, now=NOW).remaining == 1
    _reservar(session, usuario, "reposicao")
    with pytest.raises(QuotaExceededError):
        _reservar(session, usuario, "de-novo")
    assert [l.status for l in _linhas(session)].count("failed") == 1  # o ledger guarda a falha (histórico)


def test_concluidas_nao_sao_liberadas_quando_outra_falha(factory, session):
    usuario = factory.user()
    ids = consume(session, usuario, 3, now=NOW, complete=False)
    complete_generation(session, ids[0], now=NOW)
    complete_generation(session, ids[1], now=NOW)
    fail_generation(session, ids[2], error_code="ffmpeg_error", now=NOW)
    assert get_allowance(session, usuario.id, now=NOW).used == 2


# ============================================================================ transições do ledger
def test_completar_grava_fim_hash_e_duracao(factory, session):
    usuario = factory.user()
    reserva = _reservar(session, usuario, "r")
    digest = "a" * 64
    complete_generation(session, reserva.generation_id, output_sha256=digest, duration_ms=1234, now=NOW)
    (linha,) = _linhas(session)
    assert (linha.status, linha.output_sha256, linha.duration_ms, linha.finished_at) == ("completed", digest, 1234, NOW)


def test_falhar_grava_o_codigo_do_erro(factory, session):
    usuario = factory.user()
    reserva = _reservar(session, usuario, "r")
    fail_generation(session, reserva.generation_id, error_code="download_failed", now=NOW)
    (linha,) = _linhas(session)
    assert (linha.status, linha.error_code, linha.finished_at) == ("failed", "download_failed", NOW)


def test_so_reserved_pode_virar_completed_ou_failed(factory, session):
    usuario = factory.user()
    feita = _reservar(session, usuario, "a").generation_id
    falha = _reservar(session, usuario, "b").generation_id
    complete_generation(session, feita, now=NOW)
    fail_generation(session, falha, error_code="x", now=NOW)
    for operacao in (
        lambda: complete_generation(session, feita, now=NOW),  # completed -> completed
        lambda: fail_generation(session, feita, error_code="x", now=NOW),  # completed -> failed
        lambda: complete_generation(session, falha, now=NOW),  # failed -> completed
        lambda: fail_generation(session, falha, error_code="x", now=NOW),  # failed -> failed
        lambda: complete_generation(session, 999_999, now=NOW),  # inexistente
    ):
        with pytest.raises(GenerationStateError):
            operacao()
    assert [l.status for l in _linhas(session)] == ["completed", "failed"]  # nada mudou


def test_error_code_invalido(factory, session):
    reserva = _reservar(session, factory.user(), "r")
    for codigo in ("", "   ", None, "x" * 65):
        with pytest.raises(GenerationStateError):
            fail_generation(session, reserva.generation_id, error_code=codigo, now=NOW)
    assert _linhas(session)[0].status == "reserved"


def test_hash_invalido_na_conclusao_desfaz_tudo(factory, session):
    reserva = _reservar(session, factory.user(), "r")
    with pytest.raises(Exception):
        complete_generation(session, reserva.generation_id, output_sha256="curto", now=NOW)  # CHECK do banco
    session.rollback()
    assert _linhas(session)[0].status == "reserved"


# ============================================================================ idempotência
def test_mesmo_request_id_nao_cria_duas_geracoes(factory, session):
    usuario = factory.user()
    primeira = _reservar(session, usuario, "clique-1")
    repetida = _reservar(session, usuario, "clique-1")
    assert primeira.created and not repetida.created
    assert repetida.generation_id == primeira.generation_id
    assert len(_linhas(session)) == 1
    assert get_allowance(session, usuario.id, now=NOW).remaining == 4  # consumiu UMA vez só


def test_repeticao_devolve_o_estado_atual_da_reserva(factory, session):
    usuario = factory.user()
    primeira = _reservar(session, usuario, "r")
    complete_generation(session, primeira.generation_id, now=NOW)
    repetida = _reservar(session, usuario, "r")
    assert repetida.generation_id == primeira.generation_id and repetida.status == "completed" and not repetida.created


def test_repeticao_funciona_mesmo_com_a_cota_esgotada(factory, session):
    usuario = factory.user()
    for i in range(5):
        _reservar(session, usuario, f"r{i}")
    assert _reservar(session, usuario, "r0").created is False  # repetir NUNCA falha por cota
    with pytest.raises(QuotaExceededError):
        _reservar(session, usuario, "r-novo")


def test_o_mesmo_request_id_em_usuarios_diferentes_e_independente(factory, session):
    a, b = factory.user(), factory.user()
    ra, rb = _reservar(session, a, "mesmo"), _reservar(session, b, "mesmo")
    assert ra.created and rb.created and ra.generation_id != rb.generation_id


@pytest.mark.parametrize("invalido", ["", "   ", None, 123, "x" * 65], ids=["vazio", "espacos", "none", "numero", "65"])
def test_request_id_invalido(factory, session, invalido):
    usuario = factory.user()
    with pytest.raises(InvalidRequestIdError):
        reserve_generation(session, user_id=usuario.id, request_id=invalido, now=NOW)
    assert _linhas(session) == []


def test_request_id_de_64_caracteres_e_aceito(factory, session):
    assert _reservar(session, factory.user(), "x" * 64).created


# ============================================================================ erros e isolamento
def test_usuario_inexistente(factory, session):
    with pytest.raises(UserNotFoundError):
        reserve_generation(session, user_id=999_999, request_id="r", now=NOW)
    assert _linhas(session) == []


def test_recusa_por_cota_nao_afeta_outro_usuario_e_libera_o_lock(factory, session):
    cheio, livre = factory.user(), factory.user()
    consume(session, cheio, 5, now=NOW)
    with pytest.raises(QuotaExceededError):
        _reservar(session, cheio, "x")
    assert _reservar(session, livre, "y").created  # a mesma sessão segue funcionando
    assert get_allowance(session, cheio.id, now=NOW).remaining == 0


def test_sobreposicao_de_entitlements_nao_reserva_nem_cai_no_free(factory, session):
    usuario = factory.user()
    raw_entitlement(factory, usuario, plan_code="weekly", starts_at=NOW - 1 * DIA, expires_at=NOW + 6 * DIA)
    raw_entitlement(factory, usuario, plan_code="monthly", starts_at=NOW - 2 * DIA, expires_at=NOW + 28 * DIA)
    with pytest.raises(EntitlementInconsistencyError):
        _reservar(session, usuario, "r")
    assert _linhas(session) == []


def test_can_consume_acompanha_as_reservas(factory, session):
    usuario = factory.user()
    assert can_consume(session, usuario.id, now=NOW)
    consume(session, usuario, 5, now=NOW, complete=False)
    assert not can_consume(session, usuario.id, now=NOW)


# ============================================================================ reservas órfãs
def test_fail_stale_reservations_marca_so_as_antigas(factory, session):
    usuario = factory.user()
    velha = _reservar(session, usuario, "velha", now=NOW.replace(hour=9))
    recente = _reservar(session, usuario, "recente", now=NOW)
    concluida_velha = _reservar(session, usuario, "concluida", now=NOW.replace(hour=9))
    complete_generation(session, concluida_velha.generation_id, now=NOW.replace(hour=9))
    assert (NOW - NOW.replace(hour=9)).total_seconds() > GENERATION_RESERVATION_TTL_SECONDS

    assert fail_stale_reservations(session, now=NOW) == 1
    estados = {l.id: (l.status, l.error_code) for l in _linhas(session)}
    assert estados[velha.generation_id] == ("failed", "reservation_expired")
    assert estados[recente.generation_id][0] == "reserved"
    assert estados[concluida_velha.generation_id][0] == "completed"
    assert fail_stale_reservations(session, now=NOW) == 0  # repetir não faz nada
