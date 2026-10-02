"""Páginas estáticas do frontend servidas por main.py: index, Termos de Uso e Política de
Privacidade (02/10/2026 -- as duas últimas foram adicionadas depois de ficarem pendentes desde o
início do projeto; ver static/termos.html e static/privacidade.html). Sem autenticação nem banco
envolvidos -- só confere que a rota devolve o arquivo certo."""


def test_pagina_inicial(auth_client):
    resposta = auth_client.get("/")
    assert resposta.status_code == 200
    assert "KLANGO.MP4" in resposta.text


def test_termos_de_uso(auth_client):
    resposta = auth_client.get("/termos")
    assert resposta.status_code == 200
    assert "Termos de Uso" in resposta.text


def test_politica_de_privacidade(auth_client):
    resposta = auth_client.get("/privacidade")
    assert resposta.status_code == 200
    assert "Política de Privacidade" in resposta.text


def test_rodape_da_pagina_inicial_linka_as_paginas_legais(auth_client):
    """Regressão: o rodapé já teve a coluna "Legal" removida de propósito porque as páginas não
    existiam (ver histórico em static/index.html) -- agora que existem, os links precisam estar
    de volta."""
    resposta = auth_client.get("/")
    assert 'href="/termos"' in resposta.text
    assert 'href="/privacidade"' in resposta.text
