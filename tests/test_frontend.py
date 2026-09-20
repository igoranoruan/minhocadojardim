from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def _html() -> str:
    return client.get("/").text


def test_hero_aprovado():
    html = _html()
    assert "Reutilize o conteúdo que já funcionou." in html
    assert "Agora é com você." in html


def test_planos_conforme_especificacao():
    html = _html()
    assert "R$ 9,90" in html and "R$ 16,90" in html and "R$ 29,90" in html
    assert "10 downloads/dia" in html   # Semanal
    assert "20 downloads/dia" in html   # Mensal
    assert "30 downloads/dia" in html   # VIP Batch
    assert "ilimitado" not in html.lower()  # nenhum plano é ilimitado


def test_sem_promessa_de_burlar_plataformas():
    html = _html().lower()
    for termo in ("viraliz", "shadowban zero", "burlar"):
        assert termo not in html


def test_assets_em_static_sao_servidos():
    assert client.get("/static/index.html").status_code == 200
