"""GET /share-target (routes/share_target.py, 17/10/2026, suporte a Android via PWA instalável):
alvo do Web Share Target nativo do Android -- autenticado pela SESSÃO normal (cookie), diferente
do atalho do iPhone (token, ver tests/test_share_ext_routes.py). Logado, devolve uma página HTML
mínima de confirmação (que tenta se fechar sozinha, ver _pagina_confirmacao); sem sessão, devolve
um REDIRECT pra "/" com o link pendente (precisa da home pra fazer login)."""
import pytest
from fastapi.testclient import TestClient

from database.session import get_session
from helpers_generation_flow import login_directly
from main import app
from services.share_queue import list_queue

BASE_URL = "http://localhost:8000"


@pytest.fixture()
def target_client(maker):
    """Mesmo padrão de `ext_client` em tests/test_share_ext_routes.py: cliente apontado pro banco
    de teste, SEM Origin (GET está em utils.origin.SAFE_METHODS, isento por padrão -- é assim que
    o navegador chega nesta rota de verdade, uma navegação comum, nunca um fetch() com Origin)."""

    def _session():
        s = maker()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_session] = _session
    cliente = TestClient(app, base_url=BASE_URL)
    yield cliente
    app.dependency_overrides.pop(get_session, None)


def test_logado_adiciona_a_fila_e_devolve_pagina_de_confirmacao(target_client, session, factory):
    usuario = factory.user()
    login_directly(target_client, session, usuario)

    resposta = target_client.get(
        "/share-target", params={"url": "https://www.tiktok.com/@a/video/1"}, follow_redirects=False,
    )

    assert resposta.status_code == 200
    assert resposta.headers["content-type"].startswith("text/html")
    assert "Adicionado ao KLANGO" in resposta.text
    assert "window.close()" in resposta.text  # tenta voltar sozinho pro Instagram/TikTok
    assert len(list_queue(session, usuario)) == 1


def test_logado_sem_url_nem_text_so_redireciona_sem_adicionar_nada(target_client, session, factory):
    usuario = factory.user()
    login_directly(target_client, session, usuario)

    resposta = target_client.get("/share-target", follow_redirects=False)

    assert resposta.status_code == 302
    assert resposta.headers["location"] == "/"
    assert len(list_queue(session, usuario)) == 0


def test_logado_extrai_link_de_text(target_client, session, factory):
    usuario = factory.user()
    login_directly(target_client, session, usuario)

    resposta = target_client.get(
        "/share-target", params={"text": "olha isso: https://www.tiktok.com/@a/video/1"}, follow_redirects=False,
    )

    assert resposta.status_code == 200
    assert len(list_queue(session, usuario)) == 1


def test_logado_link_duplicado_mostra_mensagem_de_aviso(target_client, session, factory):
    from services.share_queue import add_to_queue

    usuario = factory.user()
    login_directly(target_client, session, usuario)
    add_to_queue(session, usuario, url="https://www.tiktok.com/@a/video/1", text=None)

    resposta = target_client.get(
        "/share-target", params={"url": "https://www.tiktok.com/@a/video/1"}, follow_redirects=False,
    )

    assert resposta.status_code == 200
    assert "já estava na fila" in resposta.text
    assert len(list_queue(session, usuario)) == 1  # não duplicou


def test_sem_sessao_com_link_redireciona_pra_compartilhar_pendente(target_client):
    resposta = target_client.get(
        "/share-target", params={"url": "https://www.tiktok.com/@a/video/1"}, follow_redirects=False,
    )

    assert resposta.status_code == 302
    assert resposta.headers["location"] == "/?compartilhar_pendente=https%3A%2F%2Fwww.tiktok.com%2F%40a%2Fvideo%2F1"


def test_sem_sessao_e_sem_link_so_manda_pra_home(target_client):
    resposta = target_client.get("/share-target", follow_redirects=False)

    assert resposta.status_code == 302
    assert resposta.headers["location"] == "/"
