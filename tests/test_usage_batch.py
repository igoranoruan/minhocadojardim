"""Reserva de lote e consumo das cotas de gerações e operações de batch.

Regras comerciais:
- Free: não usa batch.
- Semanal: 5 gerações/dia, 3 operações de batch/semana, máximo 5 vídeos por operação.
- Mensal: 10 gerações/dia, 5 operações de batch/semana, máximo 5 vídeos por operação.
- VIP Batch: 15 gerações/dia, operações de batch ilimitadas, máximo 15 vídeos por operação.

Batch usa a MESMA cota de gerações do plano: 1 geração por vídeo.
Além disso, cada chamada aceita de batch consome 1 operação da cota semanal de batch.

A reserva é tudo ou nada: se faltar saldo de vídeos ou de operações, nada é gravado.
"""

import pytest
from sqlalchemy import func, select

import services.usage as usage_service
from database.models import Batch, Generation
from helpers_usage import NOW, consume, give
from services.plans import PLANS
from services.usage import (
    BatchNotAllowedError,
    BatchQuotaExceededError,
    BatchRequestConflictError,
    InvalidBatchSizeError,
    InvalidRequestIdError,
    QuotaExceededError,
    fail_generation,
    get_allowance,
    get_batch_allowance,
    reserve_batch,
    reserve_generation,
)


PAID_PLANS = [
    (code, plano.limit, plano.max_batch_size)
    for code, plano in PLANS.items()
    if plano.paid
]


def _n(session, model):
    session.expire_all()
    return session.execute(select(func.count()).select_from(model)).scalar_one()


def _lote(session, usuario, request_id, size, **kw):
    return reserve_batch(
        session,
        user_id=usuario.id,
        request_id=request_id,
        size=size,
        now=kw.pop("now", NOW),
        **kw,
    )


@pytest.fixture()
def vip(factory, session):
    usuario = factory.user()
    give(factory, session, usuario, "vip_batch")
    return usuario


@pytest.fixture(
    params=[code for code, *_ in PAID_PLANS],
    ids=[code for code, *_ in PAID_PLANS],
)
def paid_user(request, factory, session):
    """Um usuário de cada plano pago, com o teto de lote do próprio plano."""
    usuario = factory.user()
    give(factory, session, usuario, request.param)

    limite = dict((c, l) for c, l, _ in PAID_PLANS)[request.param]
    teto = dict((c, b) for c, _, b in PAID_PLANS)[request.param]

    return usuario, request.param, limite, teto


# ============================================================================
# autorização
# ============================================================================

def test_free_nao_pode_usar_lote(factory, session):
    with pytest.raises(BatchNotAllowedError):
        _lote(session, factory.user(), "lote", 2)

    assert _n(session, Batch) == 0
    assert _n(session, Generation) == 0


def test_free_e_recusado_mesmo_com_o_tamanho_invalido(factory, session):
    with pytest.raises(BatchNotAllowedError):
        _lote(session, factory.user(), "lote", 999)

    assert _n(session, Batch) == 0
    assert _n(session, Generation) == 0


def test_todo_plano_pago_pode_reservar_lote_dentro_do_proprio_teto(
    paid_user,
    session,
):
    usuario, plano, limite, teto = paid_user

    resultado = _lote(session, usuario, "lote-1", teto)

    assert resultado.created
    assert resultado.item_count == teto
    assert resultado.plan_code == plano
    assert len(resultado.generation_ids) == teto
    assert get_allowance(session, usuario.id, now=NOW).remaining == limite - teto


# ============================================================================
# tamanho: teto por plano
# ============================================================================

def test_o_teto_do_lote_e_o_do_proprio_plano(paid_user, session):
    usuario, plano, limite, teto = paid_user

    with pytest.raises(InvalidBatchSizeError):
        _lote(session, usuario, "grande-demais", teto + 1)

    assert _n(session, Batch) == 0
    assert _n(session, Generation) == 0


def test_lote_de_1_e_permitido_em_qualquer_plano_pago(paid_user, session):
    usuario, plano, limite, teto = paid_user

    assert _lote(session, usuario, "um", 1).item_count == 1


@pytest.mark.parametrize(
    "tamanho",
    [0, -1, True, "3", 2.5, None],
    ids=["0", "negativo", "bool", "texto", "float", "none"],
)
def test_tamanho_invalido_e_rejeitado_sem_gravar_nada(vip, session, tamanho):
    with pytest.raises(InvalidBatchSizeError):
        _lote(session, vip, "lote", tamanho)

    assert _n(session, Batch) == 0
    assert _n(session, Generation) == 0


def test_request_id_invalido_no_lote(vip, session):
    with pytest.raises(InvalidRequestIdError):
        _lote(session, vip, "", 2)


# ============================================================================
# consumo e estrutura
# ============================================================================

def test_lote_consome_n_geracoes_e_grava_o_ledger(vip, session):
    resultado = _lote(
        session,
        vip,
        "lote",
        4,
        platform="tiktok",
    )

    assert get_allowance(session, vip.id, now=NOW).remaining == 11

    session.expire_all()

    (lote,) = session.execute(select(Batch)).scalars().all()

    assert (lote.user_id, lote.request_id, lote.item_count) == (
        vip.id,
        "lote",
        4,
    )

    itens = session.execute(
        select(Generation).order_by(Generation.position)
    ).scalars().all()

    assert [g.position for g in itens] == [1, 2, 3, 4]

    assert all(
        g.batch_id == lote.id
        and g.request_id is None
        and g.status == "reserved"
        for g in itens
    )

    assert all(
        g.plan_code == "vip_batch"
        and g.entitlement_id is not None
        and g.platform == "tiktok"
        for g in itens
    )

    assert [g.id for g in itens] == list(resultado.generation_ids)


def test_lote_e_avulsas_dividem_o_mesmo_limite_diario_sem_quota_de_videos_separada(
    vip,
    session,
):
    consume(session, vip, 3, now=NOW)

    _lote(session, vip, "lote", 10)

    assert get_allowance(session, vip.id, now=NOW).remaining == 2

    reserve_generation(
        session,
        user_id=vip.id,
        request_id="avulsa",
        now=NOW,
    )

    assert get_allowance(session, vip.id, now=NOW).remaining == 1


def test_um_lote_no_teto_esgota_o_dia_do_vip(vip, session):
    _lote(session, vip, "lote-cheio", 15)

    assert get_allowance(session, vip.id, now=NOW).remaining == 0

    with pytest.raises(QuotaExceededError):
        reserve_generation(
            session,
            user_id=vip.id,
            request_id="mais-um",
            now=NOW,
        )


def test_item_que_falha_libera_so_a_sua_vaga(vip, session):
    resultado = _lote(session, vip, "lote", 10)

    assert get_allowance(session, vip.id, now=NOW).remaining == 5

    fail_generation(
        session,
        resultado.generation_ids[2],
        error_code="download_failed",
        now=NOW,
    )

    fail_generation(
        session,
        resultado.generation_ids[5],
        error_code="ffmpeg_error",
        now=NOW,
    )

    assert get_allowance(session, vip.id, now=NOW).remaining == 7


# ============================================================================
# cota de operações de batch
# ============================================================================

def test_semanal_tem_3_operacoes_de_batch_por_semana(factory, session):
    usuario = factory.user()
    give(factory, session, usuario, "weekly")

    for numero in range(1, 4):
        resultado = _lote(
            session,
            usuario,
            f"weekly-batch-{numero}",
            1,
        )
        assert resultado.created

    allowance = get_batch_allowance(
        session,
        usuario.id,
        now=NOW,
    )

    assert allowance.limit == 3
    assert allowance.used == 3
    assert allowance.remaining == 0

    with pytest.raises(BatchQuotaExceededError):
        _lote(
            session,
            usuario,
            "weekly-batch-4",
            1,
        )

    assert _n(session, Batch) == 3


def test_mensal_tem_5_operacoes_de_batch_por_semana(factory, session):
    usuario = factory.user()
    give(factory, session, usuario, "monthly")

    for numero in range(1, 6):
        resultado = _lote(
            session,
            usuario,
            f"monthly-batch-{numero}",
            1,
        )
        assert resultado.created

    allowance = get_batch_allowance(
        session,
        usuario.id,
        now=NOW,
    )

    assert allowance.limit == 5
    assert allowance.used == 5
    assert allowance.remaining == 0

    with pytest.raises(BatchQuotaExceededError):
        _lote(
            session,
            usuario,
            "monthly-batch-6",
            1,
        )

    assert _n(session, Batch) == 5


def test_vip_tem_operacoes_de_batch_ilimitadas(vip, session):
    for numero in range(1, 11):
        resultado = _lote(
            session,
            vip,
            f"vip-batch-{numero}",
            1,
        )
        assert resultado.created

    allowance = get_batch_allowance(
        session,
        vip.id,
        now=NOW,
    )

    assert allowance.limit is None
    assert allowance.remaining is None
    assert allowance.unlimited
    assert allowance.used == 0

    assert _n(session, Batch) == 10


def test_cota_de_operacoes_de_batch_e_independente_da_cota_de_videos(
    factory,
    session,
):
    usuario = factory.user()
    give(factory, session, usuario, "weekly")

    # 1 operação de batch pode consumir várias gerações.
    _lote(session, usuario, "lote-1", 5)

    batch_allowance = get_batch_allowance(
        session,
        usuario.id,
        now=NOW,
    )
    generation_allowance = get_allowance(
        session,
        usuario.id,
        now=NOW,
    )

    assert batch_allowance.limit == 3
    assert batch_allowance.used == 1
    assert batch_allowance.remaining == 2

    assert generation_allowance.remaining == 0


def test_operacao_de_batch_consumida_nao_e_devolvida_quando_item_falha(
    factory,
    session,
):
    usuario = factory.user()
    give(factory, session, usuario, "weekly")

    resultado = _lote(
        session,
        usuario,
        "lote-1",
        2,
    )

    fail_generation(
        session,
        resultado.generation_ids[0],
        error_code="download_failed",
        now=NOW,
    )

    allowance = get_batch_allowance(
        session,
        usuario.id,
        now=NOW,
    )

    # A geração falhada libera sua vaga de vídeo,
    # mas a operação de batch continua consumida.
    assert allowance.used == 1
    assert allowance.remaining == 2


# ============================================================================
# atomicidade: tudo ou nada
# ============================================================================

def test_lote_sem_saldo_suficiente_e_rejeitado_inteiro(
    paid_user,
    session,
):
    usuario, plano, limite, teto = paid_user

    consume(
        session,
        usuario,
        limite - 2,
        now=NOW,
    )

    assert get_allowance(
        session,
        usuario.id,
        now=NOW,
    ).remaining == 2

    antes = (
        _n(session, Batch),
        _n(session, Generation),
    )

    with pytest.raises(QuotaExceededError) as erro:
        _lote(
            session,
            usuario,
            "tres",
            3,
        )

    assert erro.value.requested == 3
    assert erro.value.allowance.remaining == 2

    assert (
        _n(session, Batch),
        _n(session, Generation),
    ) == antes

    assert get_allowance(
        session,
        usuario.id,
        now=NOW,
    ).remaining == 2

    _lote(
        session,
        usuario,
        "dois",
        2,
    )

    assert get_allowance(
        session,
        usuario.id,
        now=NOW,
    ).remaining == 0


def test_lote_nao_e_reduzido_para_caber_no_saldo(vip, session):
    consume(
        session,
        vip,
        13,
        now=NOW,
    )

    with pytest.raises(QuotaExceededError):
        _lote(
            session,
            vip,
            "cinco",
            5,
        )

    assert _n(session, Batch) == 0
    assert _n(session, Generation) == 13
    assert get_allowance(
        session,
        vip.id,
        now=NOW,
    ).remaining == 2


def test_semanal_exemplo_do_produto_5_disponiveis_permite_1_ou_5(
    factory,
    session,
):
    usuario = factory.user()
    give(factory, session, usuario, "weekly")

    assert reserve_generation(
        session,
        user_id=usuario.id,
        request_id="individual",
        now=NOW,
    ).created

    assert get_allowance(
        session,
        usuario.id,
        now=NOW,
    ).remaining == 4

    outro = factory.user()
    give(factory, session, outro, "weekly")

    assert _lote(
        session,
        outro,
        "lote-5",
        5,
    ).item_count == 5

    assert get_allowance(
        session,
        outro.id,
        now=NOW,
    ).remaining == 0


def test_semanal_exemplo_do_produto_2_disponiveis_lote_de_3_e_recusado_lote_de_2_passa(
    factory,
    session,
):
    usuario = factory.user()
    give(factory, session, usuario, "weekly")

    consume(
        session,
        usuario,
        3,
        now=NOW,
    )

    with pytest.raises(QuotaExceededError):
        _lote(
            session,
            usuario,
            "tres",
            3,
        )

    assert _n(session, Generation) == 3

    assert _lote(
        session,
        usuario,
        "dois",
        2,
    ).item_count == 2


def test_mensal_exemplo_do_produto_maximo_diario_10_lote_maximo_5(
    factory,
    session,
):
    usuario = factory.user()
    give(factory, session, usuario, "monthly")

    assert _lote(
        session,
        usuario,
        "cinco",
        5,
    ).item_count == 5

    assert get_allowance(
        session,
        usuario.id,
        now=NOW,
    ).remaining == 5

    with pytest.raises(InvalidBatchSizeError):
        _lote(
            session,
            usuario,
            "seis",
            6,
        )


def test_vip_exemplo_do_produto_maximo_diario_15_lote_maximo_15(
    vip,
    session,
):
    assert _lote(
        session,
        vip,
        "quinze",
        15,
    ).item_count == 15

    with pytest.raises(InvalidBatchSizeError):
        _lote(
            session,
            vip,
            "dezesseis",
            16,
        )


# ============================================================================
# atomicidade da criação
# ============================================================================

def test_falha_no_meio_da_criacao_desfaz_o_lote_inteiro(
    vip,
    session,
    monkeypatch,
):
    original = usage_service._new_generation
    chamadas = []

    def falha_no_quarto_item(**kwargs):
        chamadas.append(kwargs["position"])

        if kwargs["position"] == 4:
            raise RuntimeError("falha simulada")

        return original(**kwargs)

    monkeypatch.setattr(
        usage_service,
        "_new_generation",
        falha_no_quarto_item,
    )

    with pytest.raises(RuntimeError):
        _lote(
            session,
            vip,
            "lote",
            6,
        )

    assert chamadas == [1, 2, 3, 4]
    assert _n(session, Batch) == 0
    assert _n(session, Generation) == 0
    assert get_allowance(
        session,
        vip.id,
        now=NOW,
    ).remaining == 15


# ============================================================================
# idempotência
# ============================================================================

def test_repetir_o_lote_devolve_a_mesma_reserva_sem_consumir_de_novo(
    vip,
    session,
):
    primeiro = _lote(
        session,
        vip,
        "lote",
        5,
    )

    repetido = _lote(
        session,
        vip,
        "lote",
        5,
    )

    assert primeiro.created
    assert not repetido.created

    assert (
        repetido.batch_id,
        repetido.generation_ids,
    ) == (
        primeiro.batch_id,
        primeiro.generation_ids,
    )

    assert _n(session, Batch) == 1
    assert _n(session, Generation) == 5

    assert get_allowance(
        session,
        vip.id,
        now=NOW,
    ).remaining == 10


def test_mesmo_request_id_com_outro_tamanho_e_conflito(
    vip,
    session,
):
    _lote(
        session,
        vip,
        "lote",
        5,
    )

    with pytest.raises(BatchRequestConflictError):
        _lote(
            session,
            vip,
            "lote",
            6,
        )

    assert _n(session, Generation) == 5


def test_repeticao_do_lote_vale_mesmo_sem_saldo(
    vip,
    session,
):
    _lote(
        session,
        vip,
        "lote",
        10,
    )

    consume(
        session,
        vip,
        5,
        now=NOW,
    )

    assert get_allowance(
        session,
        vip.id,
        now=NOW,
    ).remaining == 0

    assert _lote(
        session,
        vip,
        "lote",
        10,
    ).created is False


def test_lotes_de_usuarios_diferentes_com_o_mesmo_request_id(
    factory,
    session,
):
    a, b = factory.user(), factory.user()

    give(factory, session, a, "vip_batch")
    give(factory, session, b, "vip_batch")

    assert _lote(
        session,
        a,
        "mesmo",
        2,
    ).created

    assert _lote(
        session,
        b,
        "mesmo",
        2,
    ).created

    assert _n(session, Batch) == 2