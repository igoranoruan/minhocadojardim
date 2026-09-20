import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from database.session import create_db_engine, get_session
from main import app


@pytest.fixture()
def client():
    yield TestClient(app)
    app.dependency_overrides.pop(get_session, None)


class SessaoQuebrada:
    """Simula um banco indisponível."""

    def execute(self, *args, **kwargs):
        raise OperationalError("SELECT 1", {}, Exception("banco fora do ar"))

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        pass


def test_ready_200_com_banco_funcional(client, engine):
    maker = sessionmaker(bind=engine, expire_on_commit=False)

    def sessao_real():
        s = maker()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_session] = sessao_real
    resposta = client.get("/health/ready")
    assert resposta.status_code == 200
    assert resposta.json() == {"status": "ok", "database": "ok"}


def test_ready_503_quando_o_banco_esta_indisponivel(client):
    def sessao_quebrada():
        yield SessaoQuebrada()

    app.dependency_overrides[get_session] = sessao_quebrada
    resposta = client.get("/health/ready")
    assert resposta.status_code == 503
    assert resposta.json()["database"] == "unavailable"
    # sem vazar detalhes internos do erro
    assert "banco fora do ar" not in resposta.text


def test_health_continua_igual_e_independente_do_banco(client):
    def sessao_quebrada():
        yield SessaoQuebrada()

    app.dependency_overrides[get_session] = sessao_quebrada
    resposta = client.get("/health")
    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["status"] == "ok"
    assert set(corpo) == {"status", "app", "version", "env", "time_sp"}


def test_ready_503_com_banco_real_inacessivel(client, tmp_path):
    arquivo = tmp_path / "arquivo"
    arquivo.write_text("não é uma pasta")
    engine_ruim = create_db_engine(f"sqlite:///{(arquivo / 'minhoca.db').as_posix()}")
    maker = sessionmaker(bind=engine_ruim, expire_on_commit=False)

    def sessao_com_banco_ruim():
        s = maker()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_session] = sessao_com_banco_ruim
    try:
        resposta = client.get("/health/ready")
    finally:
        engine_ruim.dispose()
    assert resposta.status_code == 503
    assert resposta.json() == {"status": "unavailable", "database": "unavailable"}
