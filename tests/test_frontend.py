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
    assert "5 gerações/dia" in html    # Semanal
    assert "10 gerações/dia" in html   # Mensal
    assert "15 gerações/dia" in html   # VIP Batch
    assert "ilimitado" not in html.lower()  # nenhum plano é ilimitado

    # Os valores antigos (pré-Etapa 4.1) não podem ter sobrado em nenhum lugar da página.
    assert "10 downloads/dia" not in html
    assert "20 downloads/dia" not in html
    assert "30 downloads/dia" not in html

    # Lote: desde a Etapa 4.1, os três planos PAGOS (Semanal, Mensal, VIP Batch) oferecem lote —
    # não é mais exclusividade do VIP. O Free não tem card nesta seção (é o acesso padrão, sem
    # compra), então a garantia verificável aqui é: "Downloads em lote" aparece exatamente uma vez
    # por plano pago, nenhuma a mais — o que também comprova que o Free não ganhou a menção.
    assert html.count("Downloads em lote") == 3


def test_sem_promessa_de_burlar_plataformas():
    html = _html().lower()
    for termo in ("viraliz", "shadowban zero", "burlar"):
        assert termo not in html


def test_assets_em_static_sao_servidos():
    assert client.get("/static/index.html").status_code == 200
