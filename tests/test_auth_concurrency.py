"""Concorrência real (threads, cada uma com a sua sessão e a sua conexão) sobre um SQLite em arquivo.

Comprovam as invariáveis: nunca dois códigos abertos do mesmo e-mail aceitos ao mesmo tempo,
e um código só abre uma sessão, mesmo com verificações simultâneas.
"""
import threading
from dataclasses import replace

from sqlalchemy import text

from services.auth import AuthError, InvalidCodeError, request_login_code, verify_login_code

EMAIL = "concorrencia@example.com"
THREADS = 4


def _em_paralelo(maker, n, funcao):
    """Roda `funcao(session, i)` em n threads largando todas juntas; devolve [('ok', valor) | ('erro', exc)]."""
    barreira = threading.Barrier(n)
    resultados = [None] * n

    def trabalhador(i):
        barreira.wait(timeout=30)
        session = maker()
        try:
            resultados[i] = ("ok", funcao(session, i))
        except Exception as exc:  # o teste inspeciona o tipo de cada erro
            resultados[i] = ("erro", exc)
        finally:
            session.close()

    threads = [threading.Thread(target=trabalhador, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert all(r is not None for r in resultados), "alguma thread não terminou"
    return resultados


def _abertos(session):
    return session.execute(
        text("SELECT COUNT(*) FROM login_codes WHERE email = :e AND active_slot = 1"), {"e": EMAIL}
    ).scalar_one()


def test_pedidos_simultaneos_de_codigo_deixam_um_unico_codigo_aberto(maker, session, fake_sender, cfg):
    sem_intervalo = replace(cfg, login_code_min_interval_seconds=0)  # força a disputa pela invariável

    resultados = _em_paralelo(
        maker,
        THREADS,
        lambda s, i: request_login_code(s, email=EMAIL, ip=f"203.0.113.{i}", sender=fake_sender, cfg=sem_intervalo),
    )

    for tipo, valor in resultados:  # cada pedido ou foi atendido ou perdeu a disputa com "rate_limited"
        assert tipo == "ok" or (isinstance(valor, AuthError) and valor.code == "rate_limited"), valor
    enviados = [r for r in resultados if r[0] == "ok"]
    assert enviados, "pelo menos um pedido precisa ser atendido"

    session.expire_all()
    assert _abertos(session) == 1  # a invariável: um único código aberto

    # Dos códigos enviados por e-mail, EXATAMENTE um é aceito.
    codigos = fake_sender.codes_for(EMAIL)
    assert len(codigos) == len(enviados)
    aceitos = []
    for codigo in dict.fromkeys(codigos):  # sem repetir (colisão de 6 dígitos é possível, embora rara)
        try:
            aceitos.append(verify_login_code(session, email=EMAIL, code=codigo, cfg=sem_intervalo))
        except InvalidCodeError:
            pass
    assert len(aceitos) == 1
    assert _abertos(session) == 0


def test_verificacoes_simultaneas_do_mesmo_codigo_abrem_uma_unica_sessao(maker, session, fake_sender, cfg):
    request_login_code(session, email=EMAIL, ip="203.0.113.1", sender=fake_sender, cfg=cfg)
    codigo = fake_sender.last_code(EMAIL)

    resultados = _em_paralelo(maker, THREADS, lambda s, i: verify_login_code(s, email=EMAIL, code=codigo, cfg=cfg))

    sucessos = [valor for tipo, valor in resultados if tipo == "ok"]
    falhas = [valor for tipo, valor in resultados if tipo == "erro"]
    assert len(sucessos) == 1
    assert len(falhas) == THREADS - 1 and all(isinstance(f, InvalidCodeError) for f in falhas)

    session.expire_all()
    assert session.execute(text("SELECT COUNT(*) FROM auth_sessions")).scalar_one() == 1
    assert session.execute(text("SELECT COUNT(*) FROM users")).scalar_one() == 1
    assert _abertos(session) == 0
    assert session.execute(text("SELECT close_reason FROM login_codes")).scalar_one() == "verified"


def test_tentativas_erradas_simultaneas_sao_todas_contadas(maker, session, fake_sender, cfg):
    request_login_code(session, email=EMAIL, ip="203.0.113.1", sender=fake_sender, cfg=cfg)
    certo = fake_sender.last_code(EMAIL)
    errado = "000000" if certo != "000000" else "111111"

    resultados = _em_paralelo(maker, THREADS, lambda s, i: verify_login_code(s, email=EMAIL, code=errado, cfg=cfg))

    assert all(tipo == "erro" and isinstance(valor, InvalidCodeError) for tipo, valor in resultados)
    session.expire_all()
    assert session.execute(text("SELECT attempts FROM login_codes")).scalar_one() == THREADS  # nenhuma se perdeu
