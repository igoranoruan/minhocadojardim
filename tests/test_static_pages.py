"""Páginas estáticas do frontend servidas por main.py: index, Termos de Uso e Política de
Privacidade (02/10/2026 -- as duas últimas foram adicionadas depois de ficarem pendentes desde o
início do projeto; ver static/termos.html e static/privacidade.html). Sem autenticação nem banco
envolvidos -- só confere que a rota devolve o arquivo certo."""


def test_pagina_inicial(auth_client):
    resposta = auth_client.get("/")
    assert resposta.status_code == 200
    assert "KLANGO.MP4" in resposta.text
    # "?atalho=1" (17/10/2026): nenhum botão manda mais pra cá desde 20/10/2026 (o botão de
    # /atalho-ios agora gera e copia o token sem navegar, ver gerarTokenRapido() em
    # static/atalho-ios.html) -- o helper que consumia a flag (capturarAutoFluxoAtalhoDaUrl() em
    # static/index.html) ficou órfão mas inofensivo (só roda se alguém digitar "?atalho=1" na URL
    # na mão) e não foi removido por estar fora do escopo deste pedido do Igor; continua presente.
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


def test_termos_e_privacidade_topicos_com_mais_destaque_e_espacamento(auth_client):
    """27/10/2026 (pedido do Igor): os títulos numerados (1., 2., ...) de Termos de Uso e
    Política de Privacidade ganharam mais tamanho/peso e mais espaço entre tópicos -- o negrito já
    existia, mas o conjunto (tamanho pequeno + pouco respiro) ainda deixava os tópicos visualmente
    colados uns nos outros."""
    for rota in ("/termos", "/privacidade"):
        resposta = auth_client.get(rota)
        assert resposta.status_code == 200
        assert "font-size: 1.3rem" in resposta.text
        assert "margin-top: 3rem" in resposta.text
        assert "border-bottom: 1px solid #1b2738" in resposta.text


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


def test_pagina_de_configuracao_do_atalho_ios_tem_a_instalacao_rapida(auth_client):
    """Smoke test (17/10/2026, aprovação do CÉREBRO -- reestruturação em 3 camadas; ajustado em
    20/10/2026 por pedido do Igor depois do teste real no celular): confirma que os 3 passos da
    instalação rápida e a seção "Problemas comuns" estão presentes, que o botão de instalar aponta
    pro link real do iCloud (deixa de ser "Em breve"), e que a ordem dos passos foi invertida --
    "Instale o atalho" vem antes de "Gere o token" agora (o próprio app Atalhos pede o token durante
    a instalação, então faz mais sentido a pessoa só perceber que precisa de um nesse momento).

    20/10/2026 (pedido do Igor): o botão que antes mandava pra "/?atalho=1" (fechando esta página e
    exigindo voltar depois) virou um botão que gera e copia o token direto aqui, sem navegar pra
    lugar nenhum -- ver gerarTokenRapido() em static/atalho-ios.html. A seção "Prefere configurar
    manualmente?" foi escondida (comentada no HTML, não removida) por trazer mais objeção do que
    ajuda agora que o caminho rápido já foi confirmado funcionando de ponta a ponta."""
    resposta = auth_client.get("/atalho-ios")
    assert resposta.status_code == 200
    assert "Instalação rápida" in resposta.text
    assert "Instale o atalho" in resposta.text
    assert "Gere o token" in resposta.text
    assert "Teste compartilhando" in resposta.text
    assert "https://www.icloud.com/shortcuts/e02b6e62f2654acf8b17765878d3f721" in resposta.text
    # botão de instalar já tem o link real do iCloud (classe "em-breve" não existe mais nele --
    # a palavra "Em breve" ainda aparece num comentário HTML que documenta o histórico, por isso
    # não dá pra checar a ausência do texto puro aqui)
    assert 'class="btn-instalar em-breve"' not in resposta.text
    # "Instale o atalho" aparece ANTES de "Gere o token" no HTML (ordem invertida nesta etapa).
    assert resposta.text.index("Instale o atalho") < resposta.text.index("Gere o token")
    # botão de gerar token agora é local (chama JS, não navega pra "/?atalho=1" nem fecha a página)
    assert 'onclick="gerarTokenRapido(this)"' in resposta.text
    assert 'href="/?atalho=1"' not in resposta.text
    assert "Problemas comuns" in resposta.text
    assert "16-destaque.jpg" in resposta.text  # capturas com destaque usadas na instalação rápida
    # a seção manual foi escondida (comentada), não removida -- o texto ainda aparece na resposta
    # bruta (dentro do comentário HTML); o teste abaixo confirma que está mesmo dentro dele.
    assert "Prefere configurar manualmente?" in resposta.text


def test_pagina_de_configuracao_do_atalho_ios_tem_a_configuracao_manual_comentada_no_html(auth_client):
    """A seção "Prefere configurar manualmente?" não é renderizada como elemento visível -- ela foi
    envolvida por um comentário HTML (<!-- ... -->) em 20/10/2026, então o <details> inteiro deve
    estar DENTRO de um bloco de comentário, não solto no HTML."""
    resposta = auth_client.get("/atalho-ios")
    texto = resposta.text
    inicio_comentario = texto.index("CONFIGURAÇÃO MANUAL (conteúdo original")
    # o comentário HTML que engloba essa nota começa antes dela
    abre = texto.rindex("<!--", 0, inicio_comentario + 1)
    fecha = texto.index("-->", abre)
    posicao_details = texto.index('<details class="manual mt-6">')
    assert abre < posicao_details < fecha, "o <details> da configuração manual precisa estar dentro do comentário HTML"


def test_pagina_inicial_tem_os_dois_botoes_da_fila_do_atalho(auth_client):
    """Smoke test (17/10/2026, aprovação do CÉREBRO -- ajuste #1 da demanda de extrair áudio pela
    fila): o botão único "Enviar fila para o lote" virou dois botões lado a lado, mesmo destaque
    visual -- confere que os dois existem, com os rótulos exatos pedidos, chamando
    enviarFilaParaLote() com o formato certo, e que o botão antigo não sobrou no HTML.

    19/10/2026 (Etapa 1 da revisão de UX/copy, dossiê): os emojis 🎬/🎵 viraram ícones Font Awesome
    (fa-film/fa-music) -- o teste passa a conferir os ícones novos em vez dos emojis antigos."""
    resposta = auth_client.get("/")
    assert resposta.status_code == 200
    assert 'fa-solid fa-film' in resposta.text and "Baixar vídeos" in resposta.text
    assert 'fa-solid fa-music' in resposta.text and "Extrair áudios" in resposta.text
    assert "🎬" not in resposta.text and "🎵" not in resposta.text
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
