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


# ============================================================================ isenção de admin (Tarefa 2, pedido do CÉREBRO, 11/10/2026)
# settings.admin_email é a MESMA fonte de verdade já usada em routes/deps.py::require_admin
# (moderação de comentários) -- reaproveitada aqui, nunca um e-mail hardcoded. use_settings
# (tests/conftest.py) troca config.settings só para o teste (monkeypatch), mesmo padrão já usado
# pelos testes de autenticação/comentários.
def test_admin_consegue_extrair_a_16a_vez_sem_bloquear(factory, session, use_settings):
    use_settings(admin_email="igoranoruan@gmail.com")
    admin = factory.user(email="igoranoruan@gmail.com")
    give(factory, session, admin, "vip_batch", now=NOW)

    for i in range(AUDIO_EXTRACTIONS_PER_DAY):
        _completar(session, admin.id, f"admin-{i}", now=NOW)

    # a 16a (e a 17a) NUNCA levanta AudioQuotaExceededError para o admin
    reserva_16 = reserve_audio_extraction(session, user_id=admin.id, request_id="admin-15", now=NOW)
    assert reserva_16.created is True
    complete_audio_extraction(session, reserva_16.extraction_id, now=NOW)

    reserva_17 = reserve_audio_extraction(session, user_id=admin.id, request_id="admin-16", now=NOW)
    assert reserva_17.created is True
    complete_audio_extraction(session, reserva_17.extraction_id, now=NOW)

    # a extração do admin é GRAVADA NORMALMENTE no ledger -- "usado" reflete a realidade (17),
    # nunca congela em 15; "remaining" fica em 0 (nunca negativo, max(0, ...) já existente).
    session.expire_all()
    total = session.execute(
        select(func.count()).select_from(AudioExtraction).where(AudioExtraction.user_id == admin.id)
    ).scalar_one()
    assert total == 17
    saldo = get_audio_allowance(session, admin.id, now=NOW)
    assert saldo.used == 17 and saldo.remaining == 0
    # unlimited=True (11/10/2026, Tarefa 3): avisa quem LÊ este AudioAllowance
    # (GET /api/audio/allowance -> routes/audio.py::AllowanceOut.unlimited) que remaining==0 não
    # deve travar o botão do lote de áudio no frontend desta conta -- used/limit/remaining
    # continuam os REAIS do ledger, só o aviso muda.
    assert saldo.unlimited is True


def test_usuario_comum_continua_bloqueado_na_16a_mesmo_com_admin_email_configurado(factory, session, use_settings):
    """Mesmo teste de test_limite_diario_esgota_em_15_e_o_16o_e_recusado, mas com ADMIN_EMAIL
    configurado para OUTRO e-mail -- confirma que a isenção nunca vaza para quem não é o admin."""
    use_settings(admin_email="igoranoruan@gmail.com")
    usuario = factory.user(email="outro.usuario@example.com")
    give(factory, session, usuario, "vip_batch", now=NOW)

    for i in range(AUDIO_EXTRACTIONS_PER_DAY):
        _completar(session, usuario.id, f"r-{i}", now=NOW)

    assert get_audio_allowance(session, usuario.id, now=NOW).unlimited is False
    with pytest.raises(AudioQuotaExceededError) as exc_info:
        reserve_audio_extraction(session, user_id=usuario.id, request_id="r-16", now=NOW)
    assert exc_info.value.status_code == 429
    assert exc_info.value.detail == "Você usou suas 15 extrações de hoje. Amanhã libera de novo."


def test_sem_admin_email_configurado_ninguem_fica_isento(factory, session, use_settings):
    """ADMIN_EMAIL vazio (não configurado) -- mesmo um usuário com ESSE e-mail nunca fica isento
    (User.email nunca é vazio, então uma comparação contra "" nunca bateria mesmo sem a checagem
    explícita em _is_admin -- este teste documenta a intenção, não é só uma coincidência)."""
    use_settings(admin_email="")
    usuario = factory.user(email="igoranoruan@gmail.com")
    give(factory, session, usuario, "vip_batch", now=NOW)

    for i in range(AUDIO_EXTRACTIONS_PER_DAY):
        _completar(session, usuario.id, f"r-{i}", now=NOW)

    with pytest.raises(AudioQuotaExceededError):
        reserve_audio_extraction(session, user_id=usuario.id, request_id="r-16", now=NOW)


def test_admin_continua_sujeito_a_uma_operacao_por_vez(factory, session, use_settings):
    """A ÚNICA isenção é o teto diário -- "uma operação de áudio por vez" continua valendo
    normalmente para o admin."""
    use_settings(admin_email="igoranoruan@gmail.com")
    admin = factory.user(email="igoranoruan@gmail.com")
    give(factory, session, admin, "vip_batch", now=NOW)

    reserve_audio_extraction(session, user_id=admin.id, request_id="admin-em-andamento", now=NOW)
    with pytest.raises(AudioConcurrentOperationError):
        reserve_audio_extraction(session, user_id=admin.id, request_id="admin-outra", now=NOW)


def test_admin_consegue_reservar_lote_alem_do_saldo_diario_mas_nao_alem_do_teto_do_plano(
    factory, session, use_settings,
):
    """Lote (Parte B): a isenção também vale para reserve_audio_batch -- o admin consegue reservar
    um lote MAIOR que o saldo diário restante, mas o teto de itens por lote (Plan.max_batch_size)
    continua valendo igual para ele (não faz parte da isenção)."""
    from services.audio_usage import InvalidAudioBatchSizeError, reserve_audio_batch

    use_settings(admin_email="igoranoruan@gmail.com")
    admin = factory.user(email="igoranoruan@gmail.com")
    give(factory, session, admin, "vip_batch", now=NOW)  # vip_batch: max_batch_size=15

    for i in range(AUDIO_EXTRACTIONS_PER_DAY - 1):
        _completar(session, admin.id, f"admin-pre-{i}", now=NOW)
    saldo = get_audio_allowance(session, admin.id, now=NOW)
    assert saldo.remaining == 1

    # pede 5 (> saldo restante de 1, <= teto do plano de 15) -- não bloqueia
    reserva = reserve_audio_batch(session, user_id=admin.id, request_id="admin-lote-r1", size=5, now=NOW)
    assert reserva.item_count == 5
    for eid in reserva.extraction_ids:
        complete_audio_extraction(session, eid, now=NOW)

    # mas 16 itens (> max_batch_size=15 do vip_batch) continua recusado -- teto de PLANO, não de cota
    with pytest.raises(InvalidAudioBatchSizeError):
        reserve_audio_batch(session, user_id=admin.id, request_id="admin-lote-r2", size=16, now=NOW)
