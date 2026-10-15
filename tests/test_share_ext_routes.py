"""Rota do atalho de verdade -- POST /api/ext/share (routes/share_ext.py, 15/10/2026).

Testes obrigatórios do CÉREBRO (ajuste de aprovação da Etapa 2):
- /api/ext/share FUNCIONA sem nenhum Origin (é o cenário real do app Atalhos do iOS).
- /api/ext/share com cookie de sessão válido MAS SEM token devolve 401 -- a rota IGNORA
  completamente a sessão/cookie, só autentica por token pessoal.
"""
import pytest
from fastapi.testclient import TestClient

from helpers_generation_flow import login_directly
from main import app
from services.share_queue import generate_token, list_queue

BASE_URL = "http://localhost:8000"


def _cliente_sem_origin() -> TestClient:
    """Igual ao app real chamado pelo atalho: nenhum header de Origin/Referer."""
    return TestClient(app, base_url=BASE_URL)


def test_share_funciona_sem_nenhum_origin_header(factory, session):
    """Prova a isenção de /api/ext/ em ORIGIN_CHECK_EXEMPT_PREFIXES -- sem isso, o atalho (que
    nunca envia Origin) seria barrado pelo OriginProtectionMiddleware como qualquer outro POST."""
    usuario = factory.user()
    token = generate_token(session, usuario)

    cliente = _cliente_sem_origin()
    resposta = cliente.post("/api/ext/share", json={"url": "https://www.tiktok.com/@a/video/1", "token": token})

    assert resposta.status_code == 200
    assert resposta.headers["content-type"].startswith("text/plain")
    assert "✅" in resposta.text
    assert len(list_queue(session, usuario)) == 1


def test_share_aceita_token_no_header_authorization_bearer(factory, session):
    usuario = factory.user()
    token = generate_token(session, usuario)

    cliente = _cliente_sem_origin()
    resposta = cliente.post(
        "/api/ext/share",
        json={"url": "https://www.tiktok.com/@a/video/1"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resposta.status_code == 200
    assert len(list_queue(session, usuario)) == 1


def test_share_extrai_link_de_texto_livre(factory, session):
    usuario = factory.user()
    token = generate_token(session, usuario)

    cliente = _cliente_sem_origin()
    resposta = cliente.post(
        "/api/ext/share",
        json={"text": "olha isso: https://www.tiktok.com/@a/video/1", "token": token},
    )
    assert resposta.status_code == 200
    assert len(list_queue(session, usuario)) == 1


def test_share_sem_token_nenhum_devolve_401(engine):
    cliente = _cliente_sem_origin()
    resposta = cliente.post("/api/ext/share", json={"url": "https://www.tiktok.com/@a/video/1"})
    assert resposta.status_code == 401
    assert resposta.json()["code"] == "invalid_share_token"


def test_share_com_token_invalido_ou_revogado_devolve_401(factory, session):
    usuario = factory.user()
    token = generate_token(session, usuario)
    from services.share_queue import revoke_token

    revoke_token(session, usuario)

    cliente = _cliente_sem_origin()
    resposta_revogado = cliente.post(
        "/api/ext/share", json={"url": "https://www.tiktok.com/@a/video/1", "token": token}
    )
    assert resposta_revogado.status_code == 401

    resposta_invalido = cliente.post(
        "/api/ext/share", json={"url": "https://www.tiktok.com/@a/video/1", "token": "token-que-nao-existe"}
    )
    assert resposta_invalido.status_code == 401


def test_share_com_cookie_de_sessao_valido_mas_sem_token_devolve_401(auth_client, session, factory):
    """Teste obrigatório do CÉREBRO: a rota do atalho NUNCA autentica por cookie, mesmo que um
    cookie de sessão válido esteja presente na requisição -- só o token pessoal conta."""
    usuario = factory.user()
    login_directly(auth_client, session, usuario)  # planta um cookie de sessão válido no cliente

    resposta = auth_client.post("/api/ext/share", json={"url": "https://www.tiktok.com/@a/video/1"})
    assert resposta.status_code == 401
    assert resposta.json()["code"] == "invalid_share_token"
    assert len(list_queue(session, usuario)) == 0


def test_share_com_cookie_valido_e_token_de_outro_usuario_usa_o_dono_do_token(auth_client, session, factory):
    """O cookie presente (de `dono_do_cookie`) é completamente ignorado -- quem recebe o item na
    fila é o DONO DO TOKEN (`dono_do_token`), nunca quem está logado por cookie."""
    dono_do_cookie = factory.user()
    dono_do_token = factory.user()
    token = generate_token(session, dono_do_token)
    login_directly(auth_client, session, dono_do_cookie)

    resposta = auth_client.post(
        "/api/ext/share", json={"url": "https://www.tiktok.com/@a/video/1", "token": token}
    )
    assert resposta.status_code == 200
    assert len(list_queue(session, dono_do_token)) == 1
    assert len(list_queue(session, dono_do_cookie)) == 0


def test_share_devolve_200_com_mensagem_amigavel_para_link_duplicado(factory, session):
    """Resultado "recuperável" (link já na fila) nunca vira erro HTTP -- sempre 200 com texto
    amigável, pedido explícito do CÉREBRO (ver docstring de routes/share_ext.py)."""
    usuario = factory.user()
    token = generate_token(session, usuario)
    cliente = _cliente_sem_origin()

    cliente.post("/api/ext/share", json={"url": "https://www.tiktok.com/@a/video/1", "token": token})
    resposta = cliente.post("/api/ext/share", json={"url": "https://www.tiktok.com/@a/video/1", "token": token})

    assert resposta.status_code == 200
    assert "já estava na fila" in resposta.text
