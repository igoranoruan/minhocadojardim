from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def test_health_ok():
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["app"] == "Minhoca de Jardim"


def test_health_usa_fuso_de_sao_paulo():
    body = client.get("/health").json()
    # O Brasil não tem horário de verão desde 2019: São Paulo é sempre -03:00.
    assert body["time_sp"].endswith("-03:00")


def test_frontend_na_raiz():
    r = client.get("/")
    assert r.status_code == 200
    assert "Minhoca" in r.text


def test_health_nao_e_engolido_pelo_frontend():
    r = client.get("/health")
    assert r.headers["content-type"].startswith("application/json")
