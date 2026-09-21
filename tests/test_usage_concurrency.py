"""Concorrência real (threads, cada uma com a sua sessão e conexão) sobre um SQLite em arquivo.

Comprovam que a reserva (lock por usuário + contar + inserir) nunca deixa passar do limite.
No PostgreSQL o mesmo protocolo vale (lock de linha em vez do lock de escrita do SQLite),
mas isso só será comprovado quando esta suíte rodar em um PostgreSQL real.
"""
from sqlalchemy import func, select

from database.models import Batch, Generation
from helpers_usage import NOW, consume, give, run_parallel
from services.usage import QuotaExceededError, get_allowance, reserve_batch, reserve_generation


def _reservar_em_paralelo(maker, usuario, n, prefixo="p"):
    return run_parallel(
        maker, n, lambda s, i: reserve_generation(s, user_id=usuario.id, request_id=f"{prefixo}-{i}", now=NOW)
    )


def _separar(resultados):
    ok = [v for t, v in resultados if t == "ok"]
    erros = [v for t, v in resultados if t == "erro"]
    assert all(isinstance(e, QuotaExceededError) for e in erros), erros  # nenhum outro tipo de falha
    return ok, erros


def _total(session, model):
    session.expire_all()
    return session.execute(select(func.count()).select_from(model)).scalar_one()


def test_duas_requisicoes_para_a_ultima_geracao_so_uma_consegue(factory, session, maker):
    usuario = factory.user()
    consume(session, usuario, 4, now=NOW)  # saldo = 1
    ok, erros = _separar(_reservar_em_paralelo(maker, usuario, 2))
    assert (len(ok), len(erros)) == (1, 1)
    a = get_allowance(session, usuario.id, now=NOW)
    assert (a.used, a.remaining) == (5, 0)
    assert _total(session, Generation) == 5


def test_muitas_threads_free_nunca_passam_de_5(factory, session, maker):
    usuario = factory.user()
    ok, erros = _separar(_reservar_em_paralelo(maker, usuario, 12))
    assert (len(ok), len(erros)) == (5, 7)
    a = get_allowance(session, usuario.id, now=NOW)
    assert (a.used, a.remaining) == (5, 0)  # nunca saldo negativo nem 6 consumos
    assert _total(session, Generation) == 5


def test_varias_threads_com_saldo_parcial_reservam_exatamente_o_saldo(factory, session, maker):
    usuario = factory.user()
    consume(session, usuario, 2, now=NOW)  # saldo = 3
    ok, erros = _separar(_reservar_em_paralelo(maker, usuario, 8, prefixo="q"))
    assert (len(ok), len(erros)) == (3, 5)
    assert _total(session, Generation) == 5


def test_plano_pago_diario_tambem_e_protegido(factory, session, maker):
    usuario = factory.user()
    give(factory, session, usuario, "weekly")
    consume(session, usuario, 9, now=NOW)  # 9 de 10
    ok, erros = _separar(_reservar_em_paralelo(maker, usuario, 4))
    assert (len(ok), len(erros)) == (1, 3)
    assert get_allowance(session, usuario.id, now=NOW).remaining == 0


def test_dois_lotes_simultaneos_so_cabem_quantos_o_saldo_permitir(factory, session, maker):
    usuario = factory.user()
    give(factory, session, usuario, "vip_batch")
    consume(session, usuario, 10, now=NOW)  # saldo = 20
    resultados = run_parallel(
        maker, 3, lambda s, i: reserve_batch(s, user_id=usuario.id, request_id=f"lote-{i}", size=8, now=NOW)
    )
    ok, erros = _separar(resultados)
    assert (len(ok), len(erros)) == (2, 1)  # 8 + 8 = 16 <= 20; o terceiro (24) não cabe e é recusado INTEIRO
    assert _total(session, Batch) == 2 and _total(session, Generation) == 10 + 16
    assert get_allowance(session, usuario.id, now=NOW).remaining == 4


def test_mesmo_request_id_em_paralelo_cria_uma_unica_geracao(factory, session, maker):
    usuario = factory.user()
    resultados = run_parallel(
        maker, 6, lambda s, i: reserve_generation(s, user_id=usuario.id, request_id="clique-duplo", now=NOW)
    )
    assert all(t == "ok" for t, _ in resultados), resultados
    reservas = [v for _, v in resultados]
    assert len({r.generation_id for r in reservas}) == 1  # todas apontam para a MESMA reserva
    assert sum(1 for r in reservas if r.created) == 1
    assert _total(session, Generation) == 1
    assert get_allowance(session, usuario.id, now=NOW).used == 1


def test_o_mesmo_lote_em_paralelo_cria_um_unico_lote(factory, session, maker):
    usuario = factory.user()
    give(factory, session, usuario, "vip_batch")
    resultados = run_parallel(
        maker, 4, lambda s, i: reserve_batch(s, user_id=usuario.id, request_id="lote", size=5, now=NOW)
    )
    assert all(t == "ok" for t, _ in resultados), resultados
    assert len({v.batch_id for _, v in resultados}) == 1 and sum(1 for _, v in resultados if v.created) == 1
    assert _total(session, Batch) == 1 and _total(session, Generation) == 5


def test_usuarios_diferentes_nao_disputam_a_mesma_cota(factory, session, maker):
    a, b = factory.user(), factory.user()
    consume(session, a, 4, now=NOW, prefix="a")
    consume(session, b, 4, now=NOW, prefix="b")
    resultados = run_parallel(
        maker, 2, lambda s, i: reserve_generation(s, user_id=(a, b)[i].id, request_id=f"ultima-{i}", now=NOW)
    )
    assert all(t == "ok" for t, _ in resultados), resultados  # a última vaga de CADA um é dele
    assert get_allowance(session, a.id, now=NOW).remaining == 0
    assert get_allowance(session, b.id, now=NOW).remaining == 0
