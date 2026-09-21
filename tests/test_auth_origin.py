import pytest
from fastapi.testclient import TestClient

from main import app
from utils.origin import is_origin_allowed, origin_of

BASE = "http://localhost:8000"


def _permitido(**kw):
    dados = dict(method="POST", path="/api/auth/request-code", origin=None, referer=None, base_url=BASE)
    dados.update(kw)
    return is_origin_allowed(**dados)


# ------------------------------------------------------------------ função pura
def test_metodos_seguros_nao_exigem_origin():
    for metodo in ("GET", "HEAD", "OPTIONS"):
        assert _permitido(method=metodo)


def test_post_com_origin_igual_e_permitido():
    assert _permitido(origin=BASE)
    assert _permitido(origin="HTTP://LOCALHOST:8000")  # esquema e host sem diferença de maiúsculas


def test_origin_diferente_e_recusado():
    for origin in ("http://evil.example", "https://localhost:8000", "http://localhost:9999",
                   "http://localhost.evil.example:8000", "null", "", "lixo"):
        assert not _permitido(origin=origin), origin


def test_sem_origin_e_sem_referer_e_recusado():
    assert not _permitido()


def test_referer_e_usado_quando_nao_ha_origin():
    assert _permitido(referer="http://localhost:8000/pagina?x=1")
    assert not _permitido(referer="http://evil.example/pagina")


def test_origin_tem_prioridade_sobre_referer():
    assert not _permitido(origin="http://evil.example", referer="http://localhost:8000/")


def test_porta_padrao_e_normalizada():
    assert _permitido(origin="https://minhoca.example", base_url="https://minhoca.example:443")
    assert _permitido(origin="https://minhoca.example:443", base_url="https://minhoca.example")


def test_prefixo_de_webhook_fica_fora_da_protecao():
    assert _permitido(path="/api/webhooks/mercadopago")
    assert not _permitido(path="/api/webhook-falso/x")


def test_origin_of():
    assert origin_of("https://Exemplo.com/x?y=1") == "https://exemplo.com:443"
    assert origin_of("ftp://exemplo.com") is None
    assert origin_of("nada") is None


# ------------------------------------------------------------------ middleware (integração)
def test_post_com_origin_invalido_recebe_403_no_formato_padrao(auth_client):
    resposta = auth_client.post(
        "/api/auth/request-code", json={"email": "ana@example.com"}, headers={"Origin": "http://evil.example"}
    )
    assert resposta.status_code == 403
    assert resposta.json() == {"detail": "Origem da requisição não permitida.", "code": "invalid_origin"}
    assert resposta.headers["cache-control"] == "no-store"


def test_post_sem_origin_nem_referer_recebe_403(engine):
    cliente = TestClient(app, base_url=BASE)  # sem Origin
    resposta = cliente.post("/api/auth/logout")
    assert resposta.status_code == 403
    assert resposta.json()["code"] == "invalid_origin"


def test_post_com_referer_correto_passa_pela_checagem(auth_client):
    cliente = TestClient(app, base_url=BASE)
    resposta = cliente.post("/api/auth/logout", headers={"Referer": "http://localhost:8000/"})
    assert resposta.status_code != 403


def test_get_nao_exige_origin(engine):
    cliente = TestClient(app, base_url=BASE)
    assert cliente.get("/health").status_code == 200


def test_webhook_futuro_nao_e_barrado_pela_origem(engine):
    cliente = TestClient(app, base_url=BASE)
    resposta = cliente.post("/api/webhooks/qualquer-coisa", json={})
    assert resposta.status_code == 404  # a rota ainda não existe, mas o middleware deixou passar (não é 403)


@pytest.mark.parametrize("metodo", ["put", "patch", "delete"])
def test_outros_metodos_que_alteram_dados_tambem_sao_protegidos(engine, metodo):
    cliente = TestClient(app, base_url=BASE)
    resposta = getattr(cliente, metodo)("/api/auth/logout", headers={"Origin": "http://evil.example"})
    assert resposta.status_code == 403
