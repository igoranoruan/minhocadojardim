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


def test_geracao_usa_a_arquitetura_nova_nao_a_antiga():
    html = _html()
    # o fluxo antigo (/api/download?video_url=...) não pode mais existir
    assert '"/api/download"' not in html
    assert "/api/download?video_url=" not in html
    assert "video_url=" not in html
    # /api/download-batch (lote) é uma rota DIFERENTE, fora do escopo desta etapa -- continua existindo
    assert "/api/download-batch" in html
    # a geração individual precisa usar a rota nova
    assert '"/api/generations"' in html


def test_geracao_nao_envia_user_email():
    """user_email só pode aparecer no fluxo de LOTE (fora do escopo desta etapa) -- nunca mais na
    geração individual, que usa exclusivamente a sessão autenticada."""
    html = _html()
    inicio = html.index('fetch("/api/generations"')
    fim = html.index("async function baixarVideo", inicio) if "async function baixarVideo" in html[inicio:] else len(html)
    bloco_geracao_individual = html[inicio:inicio + 1500]
    assert "user_email" not in bloco_geracao_individual
    assert "userEmail" not in bloco_geracao_individual


def test_download_usa_generation_id_no_endpoint_novo():
    html = _html()
    assert "data.generation_id" in html
    assert "/api/generations/${data.generation_id}/download" in html
    assert "data.download_url" not in html  # o backend nunca devolveu isso; não pode ser lido


def test_campo_de_nome_de_arquivo_foi_removido():
    """O campo único de nome da geração individual (id="filename") saiu da interface. O campo de
    nome POR VÍDEO do lote (fora do escopo desta etapa, texto parecido mas de outro elemento)
    continua existindo — por isso a checagem é pelo id específico, não pelo texto do placeholder."""
    html = _html()
    assert 'id="filename"' not in html
    assert "Se deixar vazio, usamos um nome automático." not in html
    assert 'placeholder="Nome do arquivo (opcional)"' not in html
