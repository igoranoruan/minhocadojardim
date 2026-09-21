"""Reserva de lote: só o VIP, de 1 a 10 vídeos, 1 geração por vídeo, tudo ou nada."""
import pytest
from sqlalchemy import func, select

import services.usage as usage_service
from database.models import Batch, Generation
from helpers_usage import NOW, consume, give
from services.usage import (
    BatchNotAllowedError,
    BatchRequestConflictError,
    InvalidBatchSizeError,
    InvalidRequestIdError,
    QuotaExceededError,
    fail_generation,
    get_allowance,
    reserve_batch,
    reserve_generation,
)


def _n(session, model):
    session.expire_all()
    return session.execute(select(func.count()).select_from(model)).scalar_one()


def _lote(session, usuario, request_id, size, **kw):
    return reserve_batch(session, user_id=usuario.id, request_id=request_id, size=size, now=kw.pop("now", NOW), **kw)


@pytest.fixture()
def vip(factory, session):
    usuario = factory.user()
    give(factory, session, usuario, "vip_batch")
    return usuario


# ============================================================================ autorização
@pytest.mark.parametrize("plano", [None, "weekly", "monthly"], ids=["free", "semanal", "mensal"])
def test_apenas_vip_pode_usar_lote(factory, session, plano):
    usuario = factory.user()
    if plano:
        give(factory, session, usuario, plano)
    with pytest.raises(BatchNotAllowedError):
        _lote(session, usuario, "lote", 2)
    assert _n(session, Batch) == 0 and _n(session, Generation) == 0  # nada gravado


def test_vip_pode_reservar_lote(vip, session):
    resultado = _lote(session, vip, "lote-1", 3)
    assert resultado.created and resultado.item_count == 3 and resultado.plan_code == "vip_batch"
    assert len(resultado.generation_ids) == 3


def test_nao_vip_e_recusado_mesmo_com_o_tamanho_invalido(factory, session):
    with pytest.raises(BatchNotAllowedError):
        _lote(session, factory.user(), "lote", 11)  # a autorização vem antes do limite de tamanho


# ============================================================================ tamanho
def test_maximo_de_10_por_lote(vip, session):
    resultado = _lote(session, vip, "dez", 10)
    assert resultado.item_count == 10 and len(resultado.generation_ids) == 10


def test_lote_de_1_e_permitido(vip, session):
    assert _lote(session, vip, "um", 1).item_count == 1


@pytest.mark.parametrize("tamanho", [11, 0, -1, True, "3", 2.5, None], ids=["11", "0", "negativo", "bool", "texto", "float", "none"])
def test_tamanho_invalido_e_rejeitado_sem_gravar_nada(vip, session, tamanho):
    with pytest.raises(InvalidBatchSizeError):
        _lote(session, vip, "lote", tamanho)
    assert _n(session, Batch) == 0 and _n(session, Generation) == 0


def test_request_id_invalido_no_lote(vip, session):
    with pytest.raises(InvalidRequestIdError):
        _lote(session, vip, "", 2)


# ============================================================================ consumo e estrutura
def test_lote_consome_n_geracoes_e_grava_o_ledger(vip, session):
    resultado = _lote(session, vip, "lote", 4, platform="tiktok")
    assert get_allowance(session, vip.id, now=NOW).remaining == 26  # 30 - 4

    session.expire_all()
    (lote,) = session.execute(select(Batch)).scalars().all()
    assert (lote.user_id, lote.request_id, lote.item_count) == (vip.id, "lote", 4)
    itens = session.execute(select(Generation).order_by(Generation.position)).scalars().all()
    assert [g.position for g in itens] == [1, 2, 3, 4]
    assert all(g.batch_id == lote.id and g.request_id is None and g.status == "reserved" for g in itens)
    assert all(g.plan_code == "vip_batch" and g.entitlement_id is not None and g.platform == "tiktok" for g in itens)
    assert [g.id for g in itens] == list(resultado.generation_ids)


def test_lote_e_avulsas_dividem_o_mesmo_limite_diario(vip, session):
    consume(session, vip, 5, now=NOW)
    _lote(session, vip, "lote", 10)
    assert get_allowance(session, vip.id, now=NOW).remaining == 15
    reserve_generation(session, user_id=vip.id, request_id="avulsa", now=NOW)
    assert get_allowance(session, vip.id, now=NOW).remaining == 14


def test_tres_lotes_de_10_esgotam_o_dia_do_vip(vip, session):
    for i in range(3):
        _lote(session, vip, f"lote-{i}", 10)
    assert get_allowance(session, vip.id, now=NOW).remaining == 0
    with pytest.raises(QuotaExceededError):
        _lote(session, vip, "lote-4", 1)


def test_item_que_falha_libera_so_a_sua_vaga(vip, session):
    resultado = _lote(session, vip, "lote", 10)
    assert get_allowance(session, vip.id, now=NOW).remaining == 20
    fail_generation(session, resultado.generation_ids[2], error_code="download_failed", now=NOW)
    fail_generation(session, resultado.generation_ids[5], error_code="ffmpeg_error", now=NOW)
    assert get_allowance(session, vip.id, now=NOW).remaining == 22


# ============================================================================ atomicidade: tudo ou nada
def test_lote_sem_saldo_suficiente_e_rejeitado_inteiro(vip, session):
    consume(session, vip, 24, now=NOW)  # sobram 6
    assert get_allowance(session, vip.id, now=NOW).remaining == 6
    antes = (_n(session, Batch), _n(session, Generation))

    with pytest.raises(QuotaExceededError) as erro:
        _lote(session, vip, "sete", 7)
    assert erro.value.requested == 7 and erro.value.allowance.remaining == 6
    assert (_n(session, Batch), _n(session, Generation)) == antes  # NENHUMA reserva parcial
    assert get_allowance(session, vip.id, now=NOW).remaining == 6

    _lote(session, vip, "seis", 6)  # com o saldo exato, passa
    assert get_allowance(session, vip.id, now=NOW).remaining == 0


def test_falha_no_meio_da_criacao_desfaz_o_lote_inteiro(vip, session, monkeypatch):
    original = usage_service._new_generation
    chamadas = []

    def falha_no_quarto_item(**kwargs):
        chamadas.append(kwargs["position"])
        if kwargs["position"] == 4:
            raise RuntimeError("falha simulada")
        return original(**kwargs)

    monkeypatch.setattr(usage_service, "_new_generation", falha_no_quarto_item)
    with pytest.raises(RuntimeError):
        _lote(session, vip, "lote", 6)
    assert chamadas == [1, 2, 3, 4]
    assert _n(session, Batch) == 0 and _n(session, Generation) == 0  # nem o lote nem os 3 primeiros ficaram
    assert get_allowance(session, vip.id, now=NOW).remaining == 30


# ============================================================================ idempotência
def test_repetir_o_lote_devolve_a_mesma_reserva_sem_consumir_de_novo(vip, session):
    primeiro = _lote(session, vip, "lote", 5)
    repetido = _lote(session, vip, "lote", 5)
    assert primeiro.created and not repetido.created
    assert (repetido.batch_id, repetido.generation_ids) == (primeiro.batch_id, primeiro.generation_ids)
    assert _n(session, Batch) == 1 and _n(session, Generation) == 5
    assert get_allowance(session, vip.id, now=NOW).remaining == 25


def test_mesmo_request_id_com_outro_tamanho_e_conflito(vip, session):
    _lote(session, vip, "lote", 5)
    with pytest.raises(BatchRequestConflictError):
        _lote(session, vip, "lote", 6)
    assert _n(session, Generation) == 5


def test_repeticao_do_lote_vale_mesmo_sem_saldo(vip, session):
    _lote(session, vip, "lote", 10)
    consume(session, vip, 20, now=NOW)  # esgota o dia
    assert _lote(session, vip, "lote", 10).created is False


def test_lotes_de_usuarios_diferentes_com_o_mesmo_request_id(factory, session):
    a, b = factory.user(), factory.user()
    give(factory, session, a, "vip_batch")
    give(factory, session, b, "vip_batch")
    assert _lote(session, a, "mesmo", 2).created and _lote(session, b, "mesmo", 2).created
    assert _n(session, Batch) == 2
