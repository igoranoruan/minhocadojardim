"""Páginas estáticas do frontend servidas por main.py: index, Termos de Uso e Política de
Privacidade (02/10/2026 -- as duas últimas foram adicionadas depois de ficarem pendentes desde o
início do projeto; ver static/termos.html e static/privacidade.html). Sem autenticação nem banco
envolvidos -- só confere que a rota devolve o arquivo certo."""


def test_pagina_inicial(auth_client):
    resposta = auth_client.get("/")
    assert resposta.status_code == 200
    assert "KLANGO.MP4" in resposta.text
    # "?atalho=1" (17/10/2026): flag que o botão "Ir gerar meu token" de /atalho-ios manda pra cá
    # -- confirma que o helper que a consome continua presente (ver carregarAtalho() e
    # capturarAutoFluxoAtalhoDaUrl() em static/index.html).
    assert "capturarAutoFluxoAtalhoDaUrl" in resposta.text
    # PWA do Android (17/10/2026, ver routes/share_target.py e static/manifest.json): manifest
    # linkado, helper que consome "?compartilhar_pendente=" presente, e link pra /atalho-android.
    assert 'rel="manifest" href="/static/manifest.json"' in resposta.text
    assert "capturarCompartilharPendenteDaUrl" in resposta.text
    assert 'href="/atalho-android"' in resposta.text


def test_manifest_pwa(auth_client):
    resposta = auth_client.get("/static/manifest.json")
    assert resposta.status_code == 200
    dados = resposta.json()
    assert dados["name"] == "KLANGO.MP4"
    assert dados["share_target"]["action"] == "/share-target"
    assert dados["icons"][0]["src"] == "/static/icon-192.png"


def test_service_worker_servido_na_raiz_com_escopo_total(auth_client):
    """Precisa estar em /sw.js (raiz), não em /static/sw.js -- o escopo de um service worker é o
    diretório de onde ele é servido, e o Web Share Target precisa cobrir toda a origem ("/")."""
    resposta = auth_client.get("/sw.js")
    assert resposta.status_code == 200
    assert resposta.headers["content-type"].startswith("text/javascript")


def test_pagina_de_instalacao_do_atalho_android(auth_client):
    resposta = auth_client.get("/atalho-android")
    assert resposta.status_code == 200
    assert "Instalar o KLANGO.MP4 no Android" in resposta.text
    assert "Instalar app" in resposta.text


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
    "Problemas comuns" estão todos presentes -- e que o botão de instalar já aponta pro link real
    do iCloud confirmado pelo Igor (deixa de ser "Em breve")."""
    resposta = auth_client.get("/atalho-ios")
    assert resposta.status_code == 200
    assert "Instalação rápida" in resposta.text
    assert "Copie seu token" in resposta.text
    assert "Instale o atalho" in resposta.text
    assert "Teste compartilhando" in resposta.text
    assert "https://www.icloud.com/shortcuts/e02b6e62f2654acf8b17765878d3f721" in resposta.text
    # botão de instalar já tem o link real do iCloud (classe "em-breve" não existe mais nele --
    # a palavra "Em breve" ainda aparece num comentário HTML que documenta o histórico, por isso
    # não dá pra checar a ausência do texto puro aqui)
    assert 'class="btn-instalar em-breve"' not in resposta.text
    # Passo 1 ("Copie seu token") manda pra "/?atalho=1" -- index.html consome essa flag em
    # carregarAtalho() pra já rolar até a seção "Enviar para o KLANGO" e gerar o token sozinho,
    # sem a pessoa precisar rolar a home inteira e tocar em "Gerar token" (pedido do Igor,
    # 17/10/2026, ver carregarAtalho() em static/index.html).
    assert 'href="/?atalho=1"' in resposta.text
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
