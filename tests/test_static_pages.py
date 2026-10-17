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


def test_pagina_de_configuracao_do_atalho_ios(auth_client):
    """Etapa 4 (15/10/2026) -- instruções de configuração do atalho "Enviar para o KLANGO" no
    iPhone, mesmo padrão de URL limpa de /termos e /privacidade. Passo a passo manual só (16/10/2026
    -- o Igor testou em um iPhone real e confirmou que a Apple bloqueia importar um .shortcut
    baixado da web: "Não é possível importar arquivos de atalho não assinados.")."""
    resposta = auth_client.get("/atalho-ios")
    assert resposta.status_code == 200
    assert "Configurar o Atalho no iPhone" in resposta.text
    assert "api/ext/share" in resposta.text
    assert "atalho-klango.shortcut" not in resposta.text


def test_pagina_de_configuracao_do_atalho_ios_tem_a_instalacao_rapida_e_o_manual_recolhido(auth_client):
    """Smoke test (17/10/2026, aprovação do CÉREBRO -- reestruturação em 3 camadas): confirma que
    os 3 passos da instalação rápida, o <details> recolhido com o manual completo e a seção
    "Problemas comuns" estão todos presentes -- e que o botão de instalar continua "Em breve"
    (sem link quebrado pro visitante real) até o Igor mandar o link de verdade do iCloud."""
    resposta = auth_client.get("/atalho-ios")
    assert resposta.status_code == 200
    assert "Instalação rápida" in resposta.text
    assert "Copie seu token" in resposta.text
    assert "Instale o atalho" in resposta.text
    assert "Teste compartilhando" in resposta.text
    assert "Em breve" in resposta.text  # botão de instalar ainda sem o link real do iCloud
    assert "Prefere configurar manualmente?" in resposta.text
    assert "Problemas comuns" in resposta.text
    # o passo a passo manual original continua inteiro, só recolhido
    assert "Mostrar ao Compartilhar" in resposta.text
    assert "16-destaque.jpg" in resposta.text  # capturas com destaque usadas na instalação rápida
    assert "16-teste-achar-na-lista.jpg" in resposta.text  # originais, ainda usadas na seção manual


def test_pagina_inicial_tem_os_dois_botoes_da_fila_do_atalho(auth_client):
    """Smoke test (17/10/2026, aprovação do CÉREBRO -- ajuste #1 da demanda de extrair áudio pela
    fila): o botão único "Enviar fila para o lote" virou dois botões lado a lado, mesmo destaque
    visual -- confere que os dois existem, com os rótulos exatos pedidos, chamando
    enviarFilaParaLote() com o formato certo, e que o botão antigo não sobrou no HTML."""
    resposta = auth_client.get("/")
    assert resposta.status_code == 200
    assert "🎬" in resposta.text and "Baixar vídeos" in resposta.text
    assert "🎵" in resposta.text and "Extrair áudios" in resposta.text
    assert "enviarFilaParaLote('video')" in resposta.text
    assert "enviarFilaParaLote('audio')" in resposta.text
    assert "atalhoFilaEnviarBtn" not in resposta.text
    assert "Enviar fila para o lote" not in resposta.text


def test_rodape_da_pagina_inicial_linka_as_paginas_legais(auth_client):
    """Regressão: o rodapé já teve a coluna "Legal" removida de propósito porque as páginas não
    existiam (ver histórico em static/index.html) -- agora que existem, os links precisam estar
    de volta."""
    resposta = auth_client.get("/")
    assert 'href="/termos"' in resposta.text
    assert 'href="/privacidade"' in resposta.text
