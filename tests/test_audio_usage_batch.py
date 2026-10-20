"""services/audio_usage.py: reserva de LOTE de áudio (Parte B), autorização do CÉREBRO, 11/10/2026.

Mesmo padrão de tests/test_audio_usage.py (Parte A) e tests/test_usage_batch.py (lote de vídeo),
aplicado às tabelas novas `audio_batches`/`audio_extractions` -- nunca `batches`/`generations`."""
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from config import AUDIO_EXTRACTIONS_PER_DAY
from database.models import AudioBatch, AudioExtraction, Batch, Generation
from helpers_usage import NOW, give
from services.audio_usage import (
    AudioBatchRequestConflictError,
    AudioConcurrentOperationError,
    AudioNotAllowedError,
    AudioQuotaExceededError,
    InvalidAudioBatchSizeError,
    complete_audio_extraction,
    fail_audio_extraction,
    get_audio_allowance,
    get_downloadable_audio_batch_extractions,
    get_owned_audio_batch,
    reserve_audio_batch,
    reserve_audio_extraction,
)

DIA = timedelta(days=1)


def _completar_todos(session, extraction_ids, now=NOW):
    for eid in extraction_ids:
        complete_audio_extraction(session, eid, now=now)


# ============================================================================ plano pago obrigatório
def test_free_nao_consegue_reservar_lote(factory, session):
    usuario = factory.user()
    with pytest.raises(AudioNotAllowedError):
        reserve_audio_batch(session, user_id=usuario.id, request_id="lote-1", size=2, now=NOW)
    assert session.execute(select(func.count()).select_from(AudioBatch)).scalar_one() == 0
    assert session.execute(select(func.count()).select_from(AudioExtraction)).scalar_one() == 0


# ============================================================================ teto por plano (Plan.max_batch_size)
# 20/10/2026: max_batch_size de Semanal/Mensal subiu junto com a cota diária de gerações (pedido
# do Igor) -- 5->10 e 5->15. VIP Batch sem mudança.
@pytest.mark.parametrize("plano,teto", [("weekly", 10), ("monthly", 15), ("vip_batch", 15)])
def test_lote_aceita_ate_o_teto_do_plano(factory, session, plano, teto):
    usuario = factory.user()
    give(factory, session, usuario, plano, now=NOW)
    reserva = reserve_audio_batch(session, user_id=usuario.id, request_id="r-cheio", size=teto, now=NOW)
    assert reserva.created is True
    assert reserva.item_count == teto
    assert len(reserva.extraction_ids) == teto


@pytest.mark.parametrize("plano,teto", [("weekly", 10), ("monthly", 15), ("vip_batch", 15)])
def test_lote_recusa_acima_do_teto_do_plano(factory, session, plano, teto):
    usuario = factory.user()
    give(factory, session, usuario, plano, now=NOW)
    with pytest.raises(InvalidAudioBatchSizeError):
        reserve_audio_batch(session, user_id=usuario.id, request_id="r-grande", size=teto + 1, now=NOW)
    assert session.execute(select(func.count()).select_from(AudioExtraction)).scalar_one() == 0


# ============================================================================ teto por saldo diário restante
def test_lote_e_limitado_pelo_saldo_diario_restante_nao_so_pelo_plano(factory, session):
    """VIP Batch aceita até 15 por operação, mas se já restam só 4 extrações hoje, o lote de 15
    deve ser recusado com AudioQuotaExceededError (nunca InvalidAudioBatchSizeError) -- o teto
    efetivo é min(plan.max_batch_size, saldo diário restante), pedido EXATO do CÉREBRO."""
    usuario = factory.user()
    give(factory, session, usuario, "vip_batch", now=NOW)

    # consome 11 das 15 extrações diárias por extrações avulsas -- restam 4.
    for i in range(11):
        reserva = reserve_audio_extraction(session, user_id=usuario.id, request_id=f"avulsa-{i}", now=NOW)
        complete_audio_extraction(session, reserva.extraction_id, now=NOW)

    saldo = get_audio_allowance(session, usuario.id, now=NOW)
    assert saldo.remaining == 4

    with pytest.raises(AudioQuotaExceededError) as exc_info:
        reserve_audio_batch(session, user_id=usuario.id, request_id="r-lote-grande", size=5, now=NOW)
    assert exc_info.value.status_code == 429
    assert exc_info.value.detail == "Você usou suas 15 extrações de hoje. Amanhã libera de novo."
    assert session.execute(select(func.count()).select_from(AudioExtraction)).scalar_one() == 11

    # mas um lote do tamanho exato do saldo restante (4) é aceito normalmente.
    reserva_ok = reserve_audio_batch(session, user_id=usuario.id, request_id="r-lote-exato", size=4, now=NOW)
    assert reserva_ok.created is True
    assert reserva_ok.item_count == 4


# ============================================================================ uma operação de áudio por vez
def test_lote_recusado_se_ja_existe_extracao_avulsa_em_andamento(factory, session):
    usuario = factory.user()
    give(factory, session, usuario, "vip_batch", now=NOW)
    reserve_audio_extraction(session, user_id=usuario.id, request_id="avulsa-em-andamento", now=NOW)

    with pytest.raises(AudioConcurrentOperationError):
        reserve_audio_batch(session, user_id=usuario.id, request_id="r-lote", size=2, now=NOW)
    assert session.execute(select(func.count()).select_from(AudioBatch)).scalar_one() == 0


def test_avulsa_recusada_se_ja_existe_lote_em_andamento(factory, session):
    usuario = factory.user()
    give(factory, session, usuario, "vip_batch", now=NOW)
    reserve_audio_batch(session, user_id=usuario.id, request_id="r-lote", size=2, now=NOW)

    with pytest.raises(AudioConcurrentOperationError):
        reserve_audio_extraction(session, user_id=usuario.id, request_id="avulsa", now=NOW)


def test_apos_concluir_o_lote_inteiro_uma_nova_operacao_e_permitida(factory, session):
    usuario = factory.user()
    give(factory, session, usuario, "vip_batch", now=NOW)
    reserva = reserve_audio_batch(session, user_id=usuario.id, request_id="r-lote", size=2, now=NOW)
    _completar_todos(session, reserva.extraction_ids, now=NOW)

    nova = reserve_audio_extraction(session, user_id=usuario.id, request_id="avulsa-depois", now=NOW)
    assert nova.created is True


# ============================================================================ idempotência / conflito de request_id
def test_mesmo_request_id_de_lote_devolve_a_mesma_reserva(factory, session):
    usuario = factory.user()
    give(factory, session, usuario, "vip_batch", now=NOW)
    primeira = reserve_audio_batch(session, user_id=usuario.id, request_id="repete", size=3, now=NOW)
    segunda = reserve_audio_batch(session, user_id=usuario.id, request_id="repete", size=3, now=NOW)
    assert primeira.batch_id == segunda.batch_id
    assert primeira.extraction_ids == segunda.extraction_ids
    assert primeira.created is True and segunda.created is False
    assert session.execute(select(func.count()).select_from(AudioBatch)).scalar_one() == 1


def test_mesmo_request_id_com_tamanho_diferente_e_conflito(factory, session):
    usuario = factory.user()
    give(factory, session, usuario, "vip_batch", now=NOW)
    reserve_audio_batch(session, user_id=usuario.id, request_id="repete", size=3, now=NOW)
    with pytest.raises(AudioBatchRequestConflictError) as exc_info:
        reserve_audio_batch(session, user_id=usuario.id, request_id="repete", size=4, now=NOW)
    assert exc_info.value.status_code == 409


def test_mesmo_request_id_com_fingerprint_diferente_e_conflito(factory, session):
    usuario = factory.user()
    give(factory, session, usuario, "vip_batch", now=NOW)
    reserve_audio_batch(session, user_id=usuario.id, request_id="repete", size=2, content_fingerprint="fp-a", now=NOW)
    with pytest.raises(AudioBatchRequestConflictError):
        reserve_audio_batch(session, user_id=usuario.id, request_id="repete", size=2, content_fingerprint="fp-b", now=NOW)


# ============================================================================ validação estrutural de size
@pytest.mark.parametrize("tamanho_invalido", [0, -1, True])
def test_tamanho_invalido_e_recusado(factory, session, tamanho_invalido):
    usuario = factory.user()
    give(factory, session, usuario, "vip_batch", now=NOW)
    with pytest.raises(InvalidAudioBatchSizeError):
        reserve_audio_batch(session, user_id=usuario.id, request_id="r-x", size=tamanho_invalido, now=NOW)


# ============================================================================ nunca consome generation/batch de vídeo
def test_lote_de_audio_nunca_cria_linha_em_generations_ou_batches(factory, session):
    usuario = factory.user()
    give(factory, session, usuario, "vip_batch", now=NOW)
    reserva = reserve_audio_batch(session, user_id=usuario.id, request_id="r-lote", size=3, now=NOW)
    _completar_todos(session, reserva.extraction_ids, now=NOW)

    assert session.execute(select(func.count()).select_from(Generation)).scalar_one() == 0
    assert session.execute(select(func.count()).select_from(Batch)).scalar_one() == 0


def test_lote_de_audio_nao_conta_na_cota_semanal_de_lotes_do_plano(factory, session):
    """services.usage (cota semanal de operações de LOTE de vídeo, Plan.batch_limit) nunca é
    tocado -- reservar e completar vários lotes de áudio no mesmo dia não deve impedir, mais
    tarde, um lote de VÍDEO de verdade pelo mesmo usuário (camadas totalmente independentes)."""
    from services.usage import reserve_batch

    usuario = factory.user()
    give(factory, session, usuario, "weekly", now=NOW)  # batch_limit=3 operações de vídeo/semana

    for i in range(3):
        reserva = reserve_audio_batch(session, user_id=usuario.id, request_id=f"audio-lote-{i}", size=2, now=NOW)
        _completar_todos(session, reserva.extraction_ids, now=NOW)

    # ainda tem as 3 operações de lote de VÍDEO inteiras disponíveis (nenhuma foi consumida)
    lote_video = reserve_batch(session, user_id=usuario.id, request_id="video-lote-1", size=2, now=NOW)
    assert lote_video.created is True


# ============================================================================ falhas não contam no limite
def test_item_falhado_do_lote_nao_conta_no_limite_diario(factory, session):
    usuario = factory.user()
    give(factory, session, usuario, "vip_batch", now=NOW)
    reserva = reserve_audio_batch(session, user_id=usuario.id, request_id="r-lote", size=3, now=NOW)
    complete_audio_extraction(session, reserva.extraction_ids[0], now=NOW)
    complete_audio_extraction(session, reserva.extraction_ids[1], now=NOW)
    fail_audio_extraction(session, reserva.extraction_ids[2], error_code="NoAudioInPostError", now=NOW)

    saldo = get_audio_allowance(session, usuario.id, now=NOW)
    assert (saldo.used, saldo.remaining) == (2, AUDIO_EXTRACTIONS_PER_DAY - 2)


# ============================================================================ download do ZIP (elegibilidade)
def test_get_owned_audio_batch_recusa_lote_de_outro_usuario(factory, session):
    from services.audio_usage import AudioBatchNotFoundError

    dono = factory.user()
    outro = factory.user()
    give(factory, session, dono, "vip_batch", now=NOW)
    reserva = reserve_audio_batch(session, user_id=dono.id, request_id="r-lote", size=1, now=NOW)

    with pytest.raises(AudioBatchNotFoundError):
        get_owned_audio_batch(session, reserva.batch_id, outro.id)


def test_get_downloadable_audio_batch_extractions_so_devolve_itens_completed(factory, session, tmp_path, monkeypatch):
    from services import result_storage

    # result_storage real, mas isolado num diretório temporário do teste (mesmo padrão de
    # tests/test_result_storage.py::diretorio_de_storage).
    monkeypatch.setattr("services.result_storage.RESULT_STORAGE_DIR", str(tmp_path / "results"))

    usuario = factory.user()
    give(factory, session, usuario, "vip_batch", now=NOW)
    reserva = reserve_audio_batch(session, user_id=usuario.id, request_id="r-lote", size=2, now=NOW)

    origem = tmp_path / "fonte.mp3"
    origem.write_bytes(b"conteudo-mp3-fake")
    chave = result_storage.save(origem, size_bytes=origem.stat().st_size, extension="mp3")

    complete_audio_extraction(
        session, reserva.extraction_ids[0],
        output_storage_key=chave, output_expires_at=NOW + timedelta(hours=1), now=NOW,
    )
    fail_audio_extraction(session, reserva.extraction_ids[1], error_code="X", now=NOW)

    disponiveis = get_downloadable_audio_batch_extractions(session, reserva.batch_id, usuario.id, now=NOW)
    assert len(disponiveis) == 1
    assert disponiveis[0].id == reserva.extraction_ids[0]


def test_get_downloadable_audio_batch_extractions_404_generico_quando_nada_elegivel(factory, session):
    from services.audio_usage import AudioBatchNotFoundError

    usuario = factory.user()
    give(factory, session, usuario, "vip_batch", now=NOW)
    reserva = reserve_audio_batch(session, user_id=usuario.id, request_id="r-lote", size=1, now=NOW)
    fail_audio_extraction(session, reserva.extraction_ids[0], error_code="X", now=NOW)

    with pytest.raises(AudioBatchNotFoundError):
        get_downloadable_audio_batch_extractions(session, reserva.batch_id, usuario.id, now=NOW)
