"""services.usage.reserve_batch: novos parâmetros opcionais `content_fingerprint` e
`display_filenames` (Etapa 9.3, correção de idempotência real + persistência de filename).
Nenhum destes testes toca tests/test_usage_batch.py (já aprovado, fora de escopo desta correção) --
todos os cenários pré-existentes daquele arquivo continuam usando reserve_batch SEM estes
parâmetros, que têm default None (compatibilidade retroativa total, comprovada aqui também)."""
import pytest

from database.models import Batch, Generation
from helpers_usage import NOW, give
from services.batch_fingerprint import compute_batch_fingerprint
from services.usage import (
    BatchRequestConflictError,
    InvalidBatchSizeError,
    get_batch_allowance,
    reserve_batch,
)


def _plano(factory, session, usuario):
    give(factory, session, usuario, "weekly", now=NOW)


# ============================================================================ display_filenames
def test_display_filenames_e_persistido_por_posicao(factory, session):
    usuario = factory.user()
    _plano(factory, session, usuario)
    reservation = reserve_batch(
        session, user_id=usuario.id, request_id="r1", size=2, now=NOW,
        display_filenames=["a.mp4", None],
    )
    gen1, gen2 = (session.get(Generation, gid) for gid in reservation.generation_ids)
    assert gen1.display_filename == "a.mp4"
    assert gen2.display_filename is None


def test_sem_display_filenames_fica_tudo_none_regressao(factory, session):
    usuario = factory.user()
    _plano(factory, session, usuario)
    reservation = reserve_batch(session, user_id=usuario.id, request_id="r1", size=2, now=NOW)
    for gid in reservation.generation_ids:
        assert session.get(Generation, gid).display_filename is None


def test_display_filenames_com_tamanho_errado_e_rejeitado(factory, session):
    usuario = factory.user()
    _plano(factory, session, usuario)
    with pytest.raises(InvalidBatchSizeError):
        reserve_batch(
            session, user_id=usuario.id, request_id="r1", size=2, now=NOW,
            display_filenames=["a.mp4"],  # só 1, mas size=2
        )


# ============================================================================ content_fingerprint / idempotência real
def test_fingerprint_e_gravado_no_batch(factory, session):
    usuario = factory.user()
    _plano(factory, session, usuario)
    fingerprint = compute_batch_fingerprint([("https://a", None)])
    reservation = reserve_batch(
        session, user_id=usuario.id, request_id="r1", size=1, now=NOW, content_fingerprint=fingerprint,
    )
    assert session.get(Batch, reservation.batch_id).request_fingerprint == fingerprint


def test_mesmo_fingerprint_e_replay_sem_novo_batch_nem_nova_cota(factory, session):
    usuario = factory.user()
    _plano(factory, session, usuario)
    fingerprint = compute_batch_fingerprint([("https://a", None)])
    r1 = reserve_batch(session, user_id=usuario.id, request_id="r1", size=1, now=NOW, content_fingerprint=fingerprint)
    antes = get_batch_allowance(session, usuario.id, now=NOW)
    r2 = reserve_batch(session, user_id=usuario.id, request_id="r1", size=1, now=NOW, content_fingerprint=fingerprint)
    depois = get_batch_allowance(session, usuario.id, now=NOW)

    assert r1.batch_id == r2.batch_id
    assert r1.generation_ids == r2.generation_ids
    assert r2.created is False
    assert depois.used == antes.used  # nenhuma operação nova consumida


def test_fingerprint_diferente_mesmo_tamanho_e_conflito(factory, session):
    usuario = factory.user()
    _plano(factory, session, usuario)
    fp1 = compute_batch_fingerprint([("https://a", None)])
    fp2 = compute_batch_fingerprint([("https://b", None)])  # mesmo tamanho (1), URL diferente
    reserve_batch(session, user_id=usuario.id, request_id="r1", size=1, now=NOW, content_fingerprint=fp1)
    antes_batches = session.query(Batch).count()
    antes_generations = session.query(Generation).count()
    antes_cota = get_batch_allowance(session, usuario.id, now=NOW)

    with pytest.raises(BatchRequestConflictError):
        reserve_batch(session, user_id=usuario.id, request_id="r1", size=1, now=NOW, content_fingerprint=fp2)

    assert session.query(Batch).count() == antes_batches  # nenhum Batch novo
    assert session.query(Generation).count() == antes_generations  # nenhuma Generation nova
    assert get_batch_allowance(session, usuario.id, now=NOW).used == antes_cota.used  # nenhuma cota nova


def test_filename_diferente_mesmo_tamanho_e_conflito(factory, session):
    """Mesma URL, mesmo tamanho, só o FILENAME muda -- também precisa ser conflito (o fingerprint
    considera url E filename)."""
    usuario = factory.user()
    _plano(factory, session, usuario)
    fp1 = compute_batch_fingerprint([("https://a", "nome-1.mp4")])
    fp2 = compute_batch_fingerprint([("https://a", "nome-2.mp4")])
    reserve_batch(session, user_id=usuario.id, request_id="r1", size=1, now=NOW, content_fingerprint=fp1)
    with pytest.raises(BatchRequestConflictError):
        reserve_batch(session, user_id=usuario.id, request_id="r1", size=1, now=NOW, content_fingerprint=fp2)


def test_ordem_diferente_mesmo_tamanho_e_conflito(factory, session):
    usuario = factory.user()
    _plano(factory, session, usuario)
    fp1 = compute_batch_fingerprint([("https://a", None), ("https://b", None)])
    fp2 = compute_batch_fingerprint([("https://b", None), ("https://a", None)])
    reserve_batch(session, user_id=usuario.id, request_id="r1", size=2, now=NOW, content_fingerprint=fp1)
    with pytest.raises(BatchRequestConflictError):
        reserve_batch(session, user_id=usuario.id, request_id="r1", size=2, now=NOW, content_fingerprint=fp2)


# ============================================================================ compatibilidade com dados antigos
def test_batch_antigo_sem_fingerprint_nao_gera_falso_conflito(factory, session):
    """Um Batch criado ANTES desta correção (request_fingerprint=NULL, simulado aqui direto no
    banco) precisa continuar aceitando um replay com fingerprint sem virar conflito -- comparação
    é pulada quando qualquer um dos dois lados é None (ver docstring de _batch_replay)."""
    usuario = factory.user()
    _plano(factory, session, usuario)
    reserve_batch(session, user_id=usuario.id, request_id="r-antigo", size=1, now=NOW)  # sem fingerprint

    fingerprint_novo = compute_batch_fingerprint([("https://a", None)])
    resultado = reserve_batch(
        session, user_id=usuario.id, request_id="r-antigo", size=1, now=NOW, content_fingerprint=fingerprint_novo,
    )
    assert resultado.created is False  # replay normal, não conflito


def test_chamador_sem_fingerprint_nao_conflita_com_batch_que_tem_fingerprint(factory, session):
    usuario = factory.user()
    _plano(factory, session, usuario)
    fingerprint = compute_batch_fingerprint([("https://a", None)])
    reserve_batch(session, user_id=usuario.id, request_id="r1", size=1, now=NOW, content_fingerprint=fingerprint)

    resultado = reserve_batch(session, user_id=usuario.id, request_id="r1", size=1, now=NOW)  # sem fingerprint
    assert resultado.created is False