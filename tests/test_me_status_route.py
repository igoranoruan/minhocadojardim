"""GET /api/me/status (UX-1): contrato HTTP do status do usuário autenticado -- plano, uso e
validade.

Nada aqui recalcula used/limit/remaining/period ou a validade do entitlement: os cenários só
constroem dados no banco (via `factory`) e verificam que a rota TRADUZ corretamente o que
services/usage.py::get_allowance e services/entitlements.py::get_current_entitlement já decidem --
essas duas funções continuam sendo a única fonte de verdade, aqui e na rota.

A rota não aceita um `now` de teste (não faz parte do contrato -- seria abrir uma porta de
manipulação de tempo pelo cliente HTTP, o que não foi autorizado): por isso os cenários usam o
RELÓGIO REAL (utils.time_sp.now_sp/sp_day/sp_week_start), diferente dos testes de
services/usage.py, que usam um NOW fixo por aceitarem o parâmetro diretamente.
"""
from datetime import timedelta

from database.models import User
from helpers_generation_flow import anon_user_id_from_cookie, login_directly
from utils.time_sp import now_sp, sp_day, sp_week_start

URL = "/api/me/status"


def _generation(factory, usuario, plano, *, status="completed"):
    return factory.generation(
        user=usuario,
        plan_code=plano,
        status=status,
        period_day=sp_day(now_sp()),
        period_week=sp_week_start(now_sp()),
    )


def _identidade_free_do_dispositivo(auth_client, session) -> User:
    """Correção da quota Free (aprovação do CÉREBRO): sem entitlement pago -- logado ou não -- a
    identidade EFETIVA usada por get_generation_user é a do dispositivo (cookie `minhoca_anon`),
    não mais o user_id da própria sessão. Quem for pré-carregar uso/gerações para um cenário Free
    precisa primeiro estabelecer essa identidade (a primeira chamada sem cookie já cria) e usar o
    User real por trás dela -- nunca `usuario` diretamente."""
    primeira = auth_client.get(URL)
    token = primeira.headers["set-cookie"].split(";")[0].split("=", 1)[1]
    anon_id = anon_user_id_from_cookie(session, token)
    return session.get(User, anon_id)


def _entitlement(factory, usuario, plano, *, starts_at, expires_at, status="granted", **overrides):
    payment = factory.payment(usuario, plan_code=plano, status="approved")
    duration_days = overrides.pop("duration_days", max(1, round((expires_at - starts_at).total_seconds() / 86400)))
    return factory.entitlement(
        payment,
        plan_code=plano,
        duration_days=duration_days,
        starts_at=starts_at,
        expires_at=expires_at,
        status=status,
        **overrides,
    )


def _login(auth_client, session, factory):
    usuario = factory.user()
    login_directly(auth_client, session, usuario)
    return usuario


# ============================================================================ não autenticado / Free anônimo
def test_sem_sessao_devolve_status_do_visitante_anonimo(auth_client):
    """Free anônimo (aprovação do CÉREBRO): a rota usa get_generation_user, não get_current_user
    -- sem sessão, devolve o status Free (0/5) de uma identidade anônima recém-criada, nunca 401
    (é isso que permite ao frontend mostrar "usados X/5" antes de qualquer login)."""
    resposta = auth_client.get(URL)
    assert resposta.status_code == 200
    assert resposta.headers["set-cookie"].startswith("minhoca_anon=")
    assert resposta.json() == {
        "plan": {"code": "free", "name": "Free"},
        "usage": {"used": 0, "limit": 5, "remaining": 5, "period": "week"},
        "entitlement": None,
    }


# ============================================================================ Free
def test_free_sem_entitlement(auth_client, factory, session):
    _login(auth_client, session, factory)  # sem plano pago -- a cota conta na identidade do dispositivo
    identidade = _identidade_free_do_dispositivo(auth_client, session)
    _generation(factory, identidade, "free")
    _generation(factory, identidade, "free")

    resposta = auth_client.get(URL)

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["plan"] == {"code": "free", "name": "Free"}
    assert corpo["usage"] == {"used": 2, "limit": 5, "remaining": 3, "period": "week"}
    assert corpo["entitlement"] is None


# ============================================================================ planos pagos
def test_weekly_com_entitlement_vigente(auth_client, factory, session):
    usuario = _login(auth_client, session, factory)
    agora = now_sp()
    acesso = _entitlement(
        factory, usuario, "weekly", starts_at=agora - timedelta(days=1), expires_at=agora + timedelta(days=6)
    )
    for _ in range(3):
        _generation(factory, usuario, "weekly")

    resposta = auth_client.get(URL)

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["plan"] == {"code": "weekly", "name": "Semanal"}
    assert corpo["usage"] == {"used": 3, "limit": 5, "remaining": 2, "period": "day"}
    assert corpo["entitlement"] is not None
    from datetime import datetime

    devolvido = datetime.fromisoformat(corpo["entitlement"]["expires_at"])
    assert devolvido == acesso.expires_at


def test_monthly_com_entitlement_vigente_usa_o_exemplo_do_contrato(auth_client, factory, session):
    """Mesmos números do exemplo de contrato autorizado (used=4, limit=10, remaining=6)."""
    usuario = _login(auth_client, session, factory)
    agora = now_sp()
    acesso = _entitlement(
        factory, usuario, "monthly", starts_at=agora - timedelta(days=8), expires_at=agora + timedelta(days=22)
    )
    for _ in range(4):
        _generation(factory, usuario, "monthly")

    resposta = auth_client.get(URL)

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["plan"] == {"code": "monthly", "name": "Mensal"}
    assert corpo["usage"] == {"used": 4, "limit": 10, "remaining": 6, "period": "day"}
    from datetime import datetime

    devolvido = datetime.fromisoformat(corpo["entitlement"]["expires_at"])
    assert devolvido == acesso.expires_at


def test_vip_batch_com_entitlement_vigente(auth_client, factory, session):
    usuario = _login(auth_client, session, factory)
    agora = now_sp()
    _entitlement(
        factory, usuario, "vip_batch", starts_at=agora - timedelta(days=2), expires_at=agora + timedelta(days=28)
    )

    resposta = auth_client.get(URL)

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["plan"] == {"code": "vip_batch", "name": "VIP Batch"}
    assert corpo["usage"] == {"used": 0, "limit": 15, "remaining": 15, "period": "day"}
    assert corpo["entitlement"] is not None


# ============================================================================ expirado / revogado -> Free
def test_entitlement_expirado_volta_ao_free(auth_client, factory, session):
    usuario = _login(auth_client, session, factory)
    agora = now_sp()
    _entitlement(
        factory, usuario, "monthly", starts_at=agora - timedelta(days=40), expires_at=agora - timedelta(days=10)
    )
    # Entitlement expirado = sem plano pago vigente -- a cota conta na identidade do dispositivo
    identidade = _identidade_free_do_dispositivo(auth_client, session)
    _generation(factory, identidade, "free")

    resposta = auth_client.get(URL)

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["plan"] == {"code": "free", "name": "Free"}
    assert corpo["usage"] == {"used": 1, "limit": 5, "remaining": 4, "period": "week"}
    assert corpo["entitlement"] is None


def test_entitlement_revogado_volta_ao_free(auth_client, factory, session):
    usuario = _login(auth_client, session, factory)
    agora = now_sp()
    _entitlement(
        factory, usuario, "weekly",
        starts_at=agora - timedelta(days=1), expires_at=agora + timedelta(days=6),
        status="revoked", revoked_at=agora, revoke_reason="refunded",
    )

    resposta = auth_client.get(URL)

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["plan"] == {"code": "free", "name": "Free"}
    assert corpo["entitlement"] is None
