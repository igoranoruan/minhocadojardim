"""services/audio_usage.py: plano pago, limite diário (individual), concorrência e não-consumo de
geração -- Parte A (extração avulsa), autorização do CÉREBRO, 11/10/2026.

Mesmo padrão de tests/test_usage_reserve.py / tests/test_usage_allowance.py (banco de teste real,
via migrations), aplicado à tabela nova `audio_extractions` (nunca `generations`)."""
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from config import AUDIO_EXTRACTIONS_PER_DAY
from database.models import AudioExtraction, Generation
from helpers_usage import NOW, give
from services.audio_usage import (
    AudioConcurrentOperationError,
    AudioNotAllowedError,
    AudioQuotaExceededError,
    complete_audio_extraction,
    fail_audio_extraction,
    get_audio_allowance,
    reserve_audio_extraction,
)

DIA = timedelta(days=1)


def _completar(session, user_id, request_id, now=NOW):
    reserva = reserve_audio_extraction(session, user_id=user_id, request_id=request_id, now=now)
    complete_audio_extraction(session, reserva.extraction_id, now=now)
    return reserva


# ============================================================================ plano pago obrigatório
def test_free_e_bloqueado_com_403_equivalente(factory, session):
    usuario = factory.user()  # sem nenhum entitlement -> plano efetivo é Free
    with pytest.raises(AudioNotAllowedError) as exc_info:
        get_audio_allowance(session, usuario.id, now=NOW)
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Extrair áudio é exclusivo dos planos pagos."


def test_free_nao_consegue_reservar(factory, session):
    usuario = factory.user()
    with pytest.raises(AudioNotAllowedError):
        reserve_audio_extraction(session, user_id=usuario.id, request_id="r-1", now=NOW)
    assert session.execute(select(func.count()).select_from(AudioExtraction)).scalar_one() == 0


@pytest.mark.parametrize("plano", ["weekly", "monthly", "vip_batch"])
def test_plano_pago_extrai_com_sucesso(factory, session, plano):
    usuario = factory.user()
    give(factory, session, usuario, plano, now=NOW)
    saldo_antes = get_audio_allowance(session, usuario.id, now=NOW)
    assert saldo_antes.limit == AUDIO_EXTRACTIONS_PER_DAY
    assert saldo_antes.remaining == AUDIO_EXTRACTIONS_PER_DAY

    reserva = reserve_audio_extraction(session, user_id=usuario.id, request_id="r-ok", now=NOW)
    assert reserva.status == "reserved" and reserva.created is True
    complete_audio_extraction(session, reserva.extraction_id, output_sha256="a" * 64, now=NOW)

    session.expire_all()
    linha = session.get(AudioExtraction, reserva.extraction_id)
    assert linha.status == "completed"
    assert linha.output_sha256 == "a" * 64

    saldo_depois = get_audio_allowance(session, usuario.id, now=NOW)
    assert (saldo_depois.used, saldo_depois.remaining) == (1, AUDIO_EXTRACTIONS_PER_DAY - 1)


# ============================================================================ limite diário (15), somando o que já foi usado
def test_limite_diario_esgota_em_15_e_o_16o_e_recusado(factory, session):
    usuario = factory.user()
    give(factory, session, usuario, "vip_batch", now=NOW)

    for i in range(AUDIO_EXTRACTIONS_PER_DAY):
        _completar(session, usuario.id, f"r-{i}", now=NOW)

    saldo = get_audio_allowance(session, usuario.id, now=NOW)
    assert (saldo.used, saldo.remaining) == (AUDIO_EXTRACTIONS_PER_DAY, 0)

    with pytest.raises(AudioQuotaExceededError) as exc_info:
        reserve_audio_extraction(session, user_id=usuario.id, request_id="r-16", now=NOW)
    assert exc_info.value.status_code == 429
    assert exc_info.value.detail == "Você usou suas 15 extrações de hoje. Amanhã libera de novo."
    # a 16a tentativa NUNCA grava linha nenhuma (recusada antes de qualquer INSERT)
    assert session.execute(select(func.count()).select_from(AudioExtraction)).scalar_one() == 15


def test_limite_diario_reseta_no_dia_seguinte(factory, session):
    usuario = factory.user()
    give(factory, session, usuario, "vip_batch", now=NOW)
    for i in range(AUDIO_EXTRACTIONS_PER_DAY):
        _completar(session, usuario.id, f"r-{i}", now=NOW)

    amanha = NOW + DIA
    saldo_amanha = get_audio_allowance(session, usuario.id, now=amanha)
    assert (saldo_amanha.used, saldo_amanha.remaining) == (0, AUDIO_EXTRACTIONS_PER_DAY)

    # e uma nova reserva amanhã é aceita normalmente (dia novo, contador novo)
    reserva = reserve_audio_extraction(session, user_id=usuario.id, request_id="r-amanha", now=amanha)
    assert reserva.created is True


# ============================================================================ falha não conta no limite
def test_falha_nao_conta_no_limite_diario(factory, session):
    usuario = factory.user()
    give(factory, session, usuario, "weekly", now=NOW)

    reserva = reserve_audio_extraction(session, user_id=usuario.id, request_id="r-falha", now=NOW)
    fail_audio_extraction(session, reserva.extraction_id, error_code="DownloadFailedError", now=NOW)

    saldo = get_audio_allowance(session, usuario.id, now=NOW)
    assert (saldo.used, saldo.remaining) == (0, AUDIO_EXTRACTIONS_PER_DAY)

    session.expire_all()
    linha = session.get(AudioExtraction, reserva.extraction_id)
    assert linha.status == "failed" and linha.error_code == "DownloadFailedError"


# ============================================================================ uma operação de áudio por vez
def test_nao_permite_duas_reservas_simultaneas_do_mesmo_usuario(factory, session):
    usuario = factory.user()
    give(factory, session, usuario, "vip_batch", now=NOW)

    reserve_audio_extraction(session, user_id=usuario.id, request_id="r-em-andamento", now=NOW)
    with pytest.raises(AudioConcurrentOperationError) as exc_info:
        reserve_audio_extraction(session, user_id=usuario.id, request_id="r-outra", now=NOW)
    assert exc_info.value.status_code == 409
    # a tentativa concorrente NUNCA grava linha nenhuma
    assert session.execute(select(func.count()).select_from(AudioExtraction)).scalar_one() == 1


def test_apos_completar_uma_extracao_a_proxima_e_permitida(factory, session):
    usuario = factory.user()
    give(factory, session, usuario, "vip_batch", now=NOW)

    primeira = reserve_audio_extraction(session, user_id=usuario.id, request_id="r-1", now=NOW)
    complete_audio_extraction(session, primeira.extraction_id, now=NOW)

    segunda = reserve_audio_extraction(session, user_id=usuario.id, request_id="r-2", now=NOW)
    assert segunda.created is True


def test_apos_falhar_uma_extracao_a_proxima_e_permitida(factory, session):
    usuario = factory.user()
    give(factory, session, usuario, "vip_batch", now=NOW)

    primeira = reserve_audio_extraction(session, user_id=usuario.id, request_id="r-1", now=NOW)
    fail_audio_extraction(session, primeira.extraction_id, error_code="X", now=NOW)

    segunda = reserve_audio_extraction(session, user_id=usuario.id, request_id="r-2", now=NOW)
    assert segunda.created is True


# ============================================================================ idempotência
def test_mesmo_request_id_devolve_a_mesma_reserva(factory, session):
    usuario = factory.user()
    give(factory, session, usuario, "weekly", now=NOW)

    primeira = reserve_audio_extraction(session, user_id=usuario.id, request_id="repete", now=NOW)
    segunda = reserve_audio_extraction(session, user_id=usuario.id, request_id="repete", now=NOW)
    assert primeira.extraction_id == segunda.extraction_id
    assert primeira.created is True and segunda.created is False
    assert session.execute(select(func.count()).select_from(AudioExtraction)).scalar_one() == 1


# ============================================================================ nunca consome geração
def test_extracao_de_audio_nunca_cria_linha_em_generations(factory, session):
    usuario = factory.user()
    give(factory, session, usuario, "vip_batch", now=NOW)
    _completar(session, usuario.id, "r-sem-geracao", now=NOW)

    assert session.execute(select(func.count()).select_from(Generation)).scalar_one() == 0
