import re

from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def _html() -> str:
    return client.get("/").text


def test_hero_aprovado():
    html = _html()
    # 26/10/2026 (ETAPA B2, pedido do Igor -- "copy final do site"): título/subtítulo do hero
    # reescritos de novo -- "Baixe e reaproveite vídeos em 1 toque." não existe mais na página; os
    # 2 botões antigos do hero ("Testar grátis"/"Ver planos") saíram, o formulário é a chamada
    # principal.
    # 26/10/2026 (auditoria "refinamento final", aprovação explícita do Igor): "sem anúncio" virou
    # "sem anúncios no site" (deixa claro que é o site, não o vídeo, que não tem publicidade);
    # "sem metadados" virou "sem metadados da origem"; subtítulo encurtado -- as 4 plataformas já
    # aparecem nos ícones/chip "TikTok · Reels · Shorts · Pinterest" logo abaixo do formulário.
    assert "Baixe vídeos sem anúncios no site e" in html
    assert "sem metadados da origem" in html
    assert "Cole um link, escolha o nome do arquivo e receba seu MP4 pronto para editar." in html

    # Linha de preço nova, com "um por vez" e "Ver planos" como link de texto (reaproveita
    # irParaMeuPlano()).
    # 27/10/2026 (pedido do Igor, revisão de espaçamento): "Ver planos" saiu de dentro da frase
    # (quebrava no meio em telas estreitas) e virou uma linha própria -- por isso a frase não
    # termina mais em "·" seguido do link, e o link ganhou maiúscula por estar numa linha solta.
    assert "5 vídeos grátis por semana · um por vez · sem cartão</p>" in html
    assert ">Ver planos</a>" in html

    # Chips novos do topo (ETAPA B2) -- "Atalho direto do celular" saiu.
    # 27/10/2026 (pedido do Igor, revisão de espaçamento): "Lote nos planos pagos" renomeado pra
    # "Download em lote" e colocado sempre na mesma linha que "Sem anúncio" (antes, no mobile,
    # cada chip ficava numa linha separada).
    assert "Sem anúncio" in html
    assert "Download em lote" in html
    assert "Lote nos planos pagos" not in html
    assert "TikTok · Reels · Shorts · Pinterest" in html

    # Animação do topo: 2 arquivos, um por tamanho de tela (ETAPA B1).
    assert "/static/klango_hero.mp4" in html
    assert 'poster="/static/klango_hero_poster.jpg"' in html
    assert "/static/klango_hero_mobile.mp4" in html
    assert 'poster="/static/klango_hero_mobile_poster.jpg"' in html


def test_assets_antigos_da_mascote_nao_tem_mais_referencia():
    """26/10/2026 (ETAPA A): as fotos/animação antigas da mascote (topo, Lote) foram trocadas
    pelas novas ilustrações/animações no estilo do rodapé -- os arquivos antigos continuam em
    static/ (não apagados, por pedido do Igor), mas nenhuma referência a eles pode sobrar no
    HTML/JS servido.
    26/10/2026 (pedido do Igor, revertendo a ETAPA A do mesmo dia só para o modal de pagamento):
    Igor não gostou das ilustrações novas do modal e pediu para voltar às fotos antigas da
    mascote (mascote-pix.jpg/mascote-cartao.jpg) -- por isso essas 2 saíram desta lista de
    "não pode mais aparecer" (ver test_modal_de_pagamento_usa_as_fotos_da_mascote, que confirma
    que elas voltaram a aparecer no modal)."""
    html = _html()
    assert "mascote-video.jpg" not in html
    assert "mascote-lote.jpg" not in html
    assert "klango_rodape_animado" not in html


def test_secao_lote_usa_video_da_corrida_sem_caixas_estaticas():
    """26/10/2026 (ETAPA B1, pedido do Igor -- "copy final do site"): a caixa de exemplo (campos
    travados + lista video_01..03 + "Baixar tudo em ZIP") e o vídeo antigo (klango_lote.mp4) saíram
    da seção de Lote -- no lugar, o vídeo da corrida (#umPorUmDeUmaVez deixou de ser uma seção
    própria).
    26/10/2026 (auditoria "refinamento final", aprovação explícita do Igor, condicionada): as 2
    caixas de comparação "Um por um × De uma vez" (que tinham entrado na ETAPA B1 dentro de
    #loteNaPratica) saíram de vez -- o vídeo da corrida já demonstra os dois fluxos lado a lado
    com os mesmos rótulos (confirmado por inspeção visual dos frames do próprio arquivo antes
    da mudança)."""
    html = _html()
    assert "/static/klango_corrida.mp4" in html
    assert 'poster="/static/klango_corrida_poster.jpg"' in html
    assert "/static/klango_lote.mp4" not in html

    assert 'id="loteNaPraticaItems"' not in html
    assert "video_01.mp4" not in html

    assert 'id="umPorUmDeUmaVez"' not in html
    # "umPorUmDeUmaVez" não pode sobrar em SECOES_FUNIL_FRIO_IDS (só nela, nunca em comentário)
    inicio = html.index("const SECOES_FUNIL_FRIO_IDS")
    fim = html.index(";", inicio)
    assert "umPorUmDeUmaVez" not in html[inicio:fim]

    # as 2 caixas de comparação saíram da página nesta etapa -- o vídeo já cobre a mensagem.
    assert "Cola 1 link por vez" not in html
    assert "Espera terminar pra começar o próximo" not in html
    assert "Renomeia cada arquivo depois" not in html
    assert "Cola todos os links juntos" not in html
    assert "A fila roda sozinha" not in html
    assert "Escolhe o nome de cada arquivo antes de baixar" not in html


def test_secao_antes_depois_usa_video_em_vez_do_carrossel_de_cartoes():
    html = _html()
    assert "/static/klango_antes_depois.mp4" in html
    assert 'poster="/static/klango_antes_depois_poster.jpg"' in html
    # os 2 cartões antigos (com a grade de detalhes técnicos fictícios) saíram
    assert "iPhone 13" not in html
    assert "São Paulo, Brasil" not in html
    assert "3f2a8f9c" not in html


def test_secao_voce_se_reconhece_aprovada():
    """27/10/2026 (ETAPA B5, pedido do Igor): o bloco "Ainda perde tempo baixando vídeo um por
    um?" (3 caixas de dor + frase solta) foi substituído pela demonstração real do atalho "Enviar
    para o KLANGO" -- vídeo gravado no iPhone (muted/playsinline/loop/preload="none", mesmo padrão
    dos outros vídeos da página) + 3 passos numerados + CTA "Ver planos". Mesmo id
    (#voceSeReconhece), só o conteúdo mudou."""
    html = _html()
    assert "Ainda perde tempo baixando vídeo um por um?" not in html
    assert "Site cheio de anúncio e pop-up" not in html
    assert "O KLANGO resolve isso em um lugar só." not in html
    assert "DIRETO DO CELULAR" in html
    assert "Compartilhou, tá na fila." in html
    assert "O link cai direto na sua fila, sem copiar e colar." in html
    assert 'poster="/static/klango_atalho_demo_poster.jpg"' in html
    assert '<source src="/static/klango_atalho_demo.mp4" type="video/mp4">' in html
    assert "Compartilhe</b> do app que você já usa." in html
    assert "Os links chegam</b> sozinhos na sua fila." in html
    assert "Baixe tudo em ZIP</b>, já com os nomes." in html
    assert "Atalho em qualquer conta. Fila inteira em ZIP nos planos pagos." in html


def test_secao_antes_depois_textos_aprovados():
    """26/10/2026 (auditoria "refinamento final", aprovação explícita do Igor): texto encurtado --
    "com hash novo" saiu (dava a entender que isso por si só garante anonimato)."""
    html = _html()
    assert "Veja o que muda no arquivo." in html
    assert "Um novo MP4, sem os metadados da origem e com o nome que você escolher." in html


def test_secao_lote_textos_e_botao_ver_planos_aprovados():
    """26/10/2026 (auditoria "refinamento final", aprovação explícita do Igor): texto encurtado.
    27/10/2026 (pedido do Igor, revisão de espaçamento): reescrito de novo -- "...em ZIP ou"
    quebrava linha sozinho, deixando "individualmente." órfão."""
    html = _html()
    assert "Processe vários links de uma vez e baixe tudo em ZIP ou arquivo por arquivo." in html
    assert "Disponível nos planos pagos." in html
    assert ">Ver planos<" in html


def test_secao_feito_pra_titulo_aprovado():
    html = _html()
    assert "Feito pra quem trabalha com vídeo." in html


def test_secao_como_funciona_textos_aprovados():
    """26/10/2026 (ETAPA B2, pedido do Igor -- "copy final do site"): os 3 passos ganharam texto
    novo e o bloco de fechamento que havia depois deles saiu."""
    html = _html()
    assert "O KLANGO prepara" in html
    assert "Baixe e edite" in html
    assert "Cole o link do vídeo ou compartilhe direto do app pelo atalho do celular." in html
    assert "Baixa, remove os metadados e gera um MP4 novo, com hash novo." in html
    assert "Baixe com o nome que você escolheu e leve pro seu editor." in html
    assert "Limpa e prepara" not in html
    assert "Baixe e publique" not in html
    assert "Agora é com você." not in html


def test_script_unico_de_reproducao_de_videos():
    html = _html()
    assert "function inicializarReprodutorDeVideos()" in html
    assert "IntersectionObserver" in html
    assert "pausarVideosSeReducedMotion" not in html


def test_modal_de_pagamento_sem_imagem_da_mascote():
    # 26/10/2026 (pedido do Igor, teste visual): a imagem da mascote (pix/cartão) foi removida do
    # modal pra ver como fica sem ela -- o <img id="modalMascoteImg"> não existe mais na página,
    # mas a lógica de troca em trocarMetodoPagamento() continua no JS (vira no-op com o elemento
    # ausente) para voltar a funcionar sozinha se a imagem for reintroduzida depois.
    html = _html()
    assert 'id="modalMascoteImg"' not in html
    assert 'src="/static/mascote-pix.jpg"' not in html
    assert '/static/mascote-pix.jpg" : "/static/mascote-cartao.jpg"' in html


def test_planos_conforme_especificacao():
    html = _html()
    # 22/10/2026 (pedido do Igor): reajuste de preço, só valor -- limites continuam iguais.
    assert "R$ 14,90" in html and "R$ 27,90" in html and "R$ 47,90" in html
    # 20/10/2026 (pedido do Igor, 2ª rodada do mesmo dia): cota diária revista pra manter Semanal <
    # Mensal < VIP Batch também no número diário (não só no teto de lote).
    # 26/10/2026 (ETAPA B3, pedido do Igor -- "copy final do site"): "gerações" (jargão) virou
    # "vídeos" nos textos visíveis dos Planos -- confirmado antes no backend (services/plans.py:
    # "Cada vídeo processado consome 1 geração"; áudio tem cota própria, separada, em
    # services/audio_usage.py) que a troca não é enganosa.
    assert "7 vídeos/dia" in html    # Semanal
    assert "10 vídeos/dia" in html   # Mensal
    assert "15 vídeos/dia" in html   # VIP Batch
    assert "ilimitado" not in html.lower()  # nenhum plano é ilimitado

    # Itens dos Planos reescritos na ETAPA B3.
    assert "Remoção de metadados e hash novo" in html
    assert "Lotes de até 7 vídeos, 3 lotes por semana" in html
    assert "Lotes de até 10 vídeos, 5 lotes por semana" in html
    assert "Lotes de até 15 vídeos, sem limite semanal de lotes" in html
    assert "Pra uma semana de muito volume." in html
    assert "Pra quem posta toda semana." in html
    assert "Pra quem processa lote todo dia." in html
    assert ">Escolher Semanal<" in html
    assert ">Quero o Mensal<" in html
    assert ">Quero o VIP Batch<" in html
    assert (
        "Pagamento único no Pix ou cartão · sem renovação automática · acesso liberado assim que o pagamento confirma."
        in html
    )

    # Os valores antigos (pré-Etapa 4.1) não podem ter sobrado em nenhum lugar da página.
    assert "10 downloads/dia" not in html
    assert "20 downloads/dia" not in html
    assert "30 downloads/dia" not in html

    # Lote: desde a Etapa 4.1, os três planos PAGOS (Semanal, Mensal, VIP Batch) oferecem lote —
    # não é mais exclusividade do VIP. O Free não tem card nesta seção (é o acesso padrão, sem
    # compra), então a garantia verificável aqui é: "Downloads em lote" aparece exatamente uma vez
    # por plano pago, nenhuma a mais — o que também comprova que o Free não ganhou a menção.
    assert html.count("Downloads em lote") == 3


def test_faq_cinco_perguntas_sempre_visiveis_na_ordem_certa():
    """26/10/2026 (auditoria "refinamento final", aprovação explícita do Igor -- substitui a
    decisão anterior da ETAPA B3 de 12 sempre visíveis): só 5 perguntas ficam sempre visíveis
    agora, nesta ordem exata; as outras 9 (nenhuma removida) ficam dentro de #faqMais, atrás do
    "Ver mais perguntas"."""
    html = _html()
    inicio = html.index('<section id="faq"')
    fim_visiveis = html.index('id="faqMais"', inicio)
    fim_secao = html.index("</section>", inicio)
    visiveis_html = html[inicio:fim_visiveis]
    mais_html = html[fim_visiveis:fim_secao]

    perguntas_sempre_visiveis = [
        "O que é o KLANGO.MP4 e como funciona?",
        "Quais plataformas são compatíveis?",
        "O que posso fazer no plano grátis?",
        "Como funciona o lote?",
        "Como funcionam o pagamento e a validade do plano?",
    ]
    posicoes = [visiveis_html.index(p) for p in perguntas_sempre_visiveis]
    assert posicoes == sorted(posicoes)
    # nenhuma das 5 pode aparecer de novo dentro de "Ver mais" (senão duplicou)
    for pergunta in perguntas_sempre_visiveis:
        assert pergunta not in mais_html

    # As outras 9 perguntas da ETAPA B3 + as 2 que já existiam em "Ver mais": nenhuma foi
    # removida, só reposicionada pra dentro de #faqMais.
    perguntas_em_ver_mais = [
        "Qual plano devo escolher?",
        "Posso escolher o nome dos arquivos?",
        "Funciona no celular? Preciso instalar algo?",
        "O KLANGO pede acesso às minhas redes sociais?",
        "Meus vídeos ficam guardados? Por quanto tempo?",
        "O KLANGO.MP4 garante que meu conteúdo não será identificado pelas plataformas?",
        "E se der problema no pagamento ou no vídeo?",
        "O que muda tecnicamente no arquivo?",
        "Por que o YouTube demora mais?",
    ]
    for pergunta in perguntas_em_ver_mais:
        assert pergunta in mais_html

    # Perguntas antigas que já tinham saído do FAQ na ETAPA B3 (continuam fora).
    for pergunta_antiga in (
        "Por que usar o KLANGO.MP4 em vez de uma ferramenta gratuita?",
        "Quantas gerações grátis eu tenho?",
        "Preciso instalar alguma coisa?",
        "Funciona no celular?<i",  # a versão antiga, sem "Preciso instalar algo?" junto
        "Meus dados e minhas contas ficam seguros?",
        "O que acontece com o vídeo depois de pronto?",
        "O KLANGO.MP4 armazena meus vídeos?",
        "O pagamento é único ou tem renovação automática?",
        "O plano expira?",
        "Posso pagar com PIX?",
        "Posso pagar com cartão?",
        "Posso ser reembolsado?",
        "Como funciona o processamento em lote?",
        "Como funciona o acesso?",
    ):
        assert pergunta_antiga not in html[inicio:fim_secao]


def test_faq_toggle_texto_alterna_para_ver_menos_perguntas():
    """26/10/2026 (auditoria "refinamento final", aprovação explícita do Igor): ao expandir, o
    botão deve mudar para "Ver menos perguntas" (antes só virava "Ver menos").
    26/10/2026 (validação pré-publicação, aprovação explícita do Igor): teste reforçado -- antes
    só conferia se as 2 strings apareciam em algum lugar da função, sem checar o lado certo do
    ternário; agora confere a expressão exata, então uma inversão acidental do ternário faria o
    teste falhar."""
    html = _html()
    assert 'estavaEscondido ? "Ver menos perguntas" : "Ver mais perguntas"' in html


def test_comunidade_movida_pra_depois_do_faq_com_texto_novo():
    html = _html()
    pos_faq = html.index('<section id="faq"')
    pos_comunidade = html.index('<section id="comentarios"')
    pos_cta = html.index('<section id="ctaFinal"')
    assert pos_faq < pos_comunidade < pos_cta

    assert "Comunidade KLANGO.MP4" in html
    assert "Usa o KLANGO na sua rotina? Conte como foi. Seu comentário pode aparecer aqui." in html
    assert "Estamos começando por aqui" not in html
    assert "O que quem usa está dizendo." not in html
    assert "Primeiros usuários do KLANGO.MP4" not in html


def test_chamada_final_aprovada():
    html = _html()
    assert "Teste agora com 5 vídeos grátis por semana." in html
    assert "Um por vez, sem cartão." in html
    assert "Comece agora com 5 vídeos grátis por semana." not in html


def test_login_textos_aprovados():
    """26/10/2026 (ETAPA B4, pedido do Igor -- "copy final do site"): textos do passo de e-mail e
    do passo de código, só copy -- pedirCodigo()/confirmarCodigo() não foram tocados."""
    html = _html()
    assert "Entre sem criar senha." in html
    assert "Informe seu e-mail e receba um código de 6 dígitos." in html
    assert "Sem senha" in html
    assert "Código no e-mail" in html
    assert "Seus dados protegidos" in html
    assert "Ele vale por 10 minutos." in html
    assert ">Usar outro e-mail<" in html
    assert ">Trocar e-mail<" not in html


def test_modal_de_pagamento_textos_e_estrutura_aprovados():
    """26/10/2026 (pedido do Igor, revertendo a ETAPA B4 do mesmo dia): Igor achou que a versão de
    1 coluna perdeu informação útil e pediu para voltar título/subtítulo/textos/coluna de volta ao
    estado anterior -- lógica de pagamento (métodos, Brick, submissão) não foi tocada, só
    textos/estrutura/CSS do modal."""
    html = _html()
    assert "Escolha como pagar" in html
    assert "Finalize seu acesso de forma rápida e segura" in html
    assert "Processado com segurança pelo Mercado Pago." in html
    assert "O pagamento é único. Não há cobrança automática nem renovação." in html
    assert "Por que escolher o KLANGO.MP4?" in html
    assert "Vídeos limpos de metadados" in html
    assert "Ideal para criadores e clipadores" in html
    assert 'id="paymentBrickContainer"' in html
    assert 'id="metodoPixBtn"' in html
    assert 'id="metodoCartaoBtn"' in html


def test_area_logada_textos_aprovados():
    html = _html()
    assert "Cada arquivo fica disponível por até 30 minutos depois de pronto." in html
    assert "Compartilhe o vídeo direto do TikTok, Instagram, YouTube ou Pinterest e o link cai na sua fila aqui. Sem login nem senha das redes." in html
    assert "Compartilhe seu link. Quando alguém assinar por ele, você ganha 7 dias a mais de acesso." in html
    # 26/10/2026 (ETAPA B4): "gerações" -> "vídeos" também na mensagem de erro do lote.
    assert "Os vídeos desse lote foram devolvidos, tente de novo." in html
    assert "As gerações desse lote foram devolvidas" not in html


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
    """Desde a barra de progresso real (SSE), o generation_id vem do evento `complete` do stream
    (dados.generation_id, dentro de exibirSucessoDaGeracao), não mais de um `await
    response.json()` direto sobre a resposta -- mas o endpoint de download continua o mesmo,
    montado a partir do generation_id."""
    html = _html()
    assert "dados.generation_id" in html
    assert "/api/generations/${dados.generation_id}/download" in html
    assert "dados.download_url" not in html  # o backend nunca devolveu isso; não pode ser lido
    assert "data.download_url" not in html
    assert "data.generation_id" not in html  # contrato antigo (pré-SSE): não pode ter sobrado


def test_campo_de_nome_de_arquivo_foi_removido():
    """O campo único de nome da geração individual (id="filename") saiu da interface. O campo de
    nome POR VÍDEO do lote (fora do escopo desta etapa, texto parecido mas de outro elemento)
    continua existindo — por isso a checagem é pelo id específico, não pelo texto do placeholder."""
    html = _html()
    assert 'id="filename"' not in html
    assert "Se deixar vazio, usamos um nome automático." not in html


# ============================================================================ extração de áudio individual (Parte A, autorização do CÉREBRO, 11/10/2026)
def test_toggle_de_formato_individual_existe_e_video_e_o_padrao():
    """O botão "Processar e baixar" passa a chamar processarIndividual() (dispatcher novo) em vez
    de baixarVideo() direto -- mas baixarVideo() continua existindo intacta (chamada pelo
    dispatcher quando o formato é "video", o padrão)."""
    html = _html()
    assert 'onclick="processarIndividual()"' in html
    assert "async function baixarVideo" in html  # função de vídeo nunca foi removida/renomeada
    assert "async function baixarAudio" in html
    assert 'let formatoIndividual = "video";' in html  # padrão inicial


def test_free_sempre_ve_os_dois_botoes_de_formato():
    """Pedido EXATO do CÉREBRO: a opção de áudio nunca é escondida do Free/deslogado -- só fica
    bloqueada (cadeado) quando selecionada sem plano pago."""
    html = _html()
    assert 'id="formatoVideoBtn"' in html
    assert 'id="formatoAudioBtn"' in html
    assert "Vídeo (MP4)" in html
    assert "Só áudio (MP3)" in html


def test_filename_input_tem_placeholder_curto():
    """26/10/2026 (auditoria "refinamento final", aprovação explícita do Igor): placeholder do
    campo individual (#filenameInput) encurtado pra "Nome do arquivo (opcional)" (sem exemplo), no
    HTML inicial e nas 2 trocas de selecionarFormatoIndividual() (vídeo/áudio). Escopo só deste
    campo -- os placeholders do fluxo de lote (campoTravado) não foram alterados nesta etapa."""
    html = _html()
    assert 'id="filenameInput"\n' in html or 'id="filenameInput" ' in html
    assert 'placeholder="Nome do arquivo (opcional)"' in html
    corpo = re.search(r"function selecionarFormatoIndividual\(formato\) \{(.*?)\n    \}", html, re.S).group(1)
    assert corpo.count('placeholder = "Nome do arquivo (opcional)"') == 2
    assert "ex.:" not in corpo


def test_bloco_de_cadeado_do_audio_individual_usa_a_mensagem_exata():
    html = _html()
    assert 'id="audioBloqueadoIndividual"' in html
    assert "Extrair áudio é exclusivo dos planos pagos." in html


def test_extracao_de_audio_usa_a_rota_sincrona_nova_sem_sse():
    html = _html()
    assert '"/api/audio/extract"' in html
    assert '"/api/audio/allowance"' in html
    assert "/api/audio/${dados.extraction_id}/download" in html
    # 26/10/2026 (auditoria "refinamento final", aprovação explícita do Igor): o placeholder
    # voltou a ser o texto curto "Nome do arquivo (opcional)" (sem "— ex.: video_oferta"/"—
    # ex.: audio_oferta") -- ver test_filename_input_tem_placeholder_curto.


def test_barra_de_progresso_nao_recria_o_dom_a_cada_evento():
    """A correção de UX: o bloco de progresso (texto + barra) tem que ser criado com ids fixos e
    reaproveitado -- exibirProgressoReal/exibirProgressoIndeterminado só devem tocar
    innerHTML/setStatusBox quando o bloco ainda NÃO existe; do contrário, só textContent/
    style.width. Isso é o que evita o "piscar" (animate-fade reiniciando a cada tick de progresso).
    """
    html = _html()
    # ids estáveis, usados tanto para criar quanto para localizar o bloco já existente
    assert 'id="progressoTexto"' in html
    assert 'id="progressoBarra"' in html
    assert 'id="progressoIndeterminadoTexto"' in html

    # as duas funções precisam checar a existência do bloco ANTES de decidir recriar
    assert 'getElementById("progressoTexto")' in html
    assert 'getElementById("progressoBarra")' in html
    assert 'getElementById("progressoIndeterminadoTexto")' in html

    # quando o bloco já existe, a atualização é só textContent/style.width -- nunca innerHTML
    assert ".textContent = `${mensagem" in html or ".textContent = mensagem" in html
    assert "barraExistente.style.width" in html


# ============================================================================ extração de áudio em LOTE (Parte B, autorização do CÉREBRO, 11/10/2026)
def test_toggle_de_formato_do_lote_existe_e_video_e_o_padrao():
    """O botão único do lote (#batchButton) passa a chamar processarLoteFormato() (dispatcher) em
    vez de processarLote() direto -- mas processarLote() continua existindo intacta (chamada pelo
    dispatcher quando o formato é "video", o padrão)."""
    html = _html()
    assert 'onclick="processarLoteFormato()"' in html
    assert "async function processarLote(" in html  # função de vídeo nunca foi removida/renomeada
    assert "async function processarLoteAudio" in html
    assert 'let formatoLote = "video";' in html  # padrão inicial


def test_toggle_do_lote_tem_os_dois_botoes_de_formato():
    html = _html()
    assert 'id="formatoVideoBtnLote"' in html
    assert 'id="formatoAudioBtnLote"' in html


def test_lote_de_audio_usa_a_rota_sse_nova_e_o_zip_com_nome_fixo():
    html = _html()
    assert '"/api/audio/batch"' in html
    assert '"/api/audio/allowance"' in html
    assert "/api/audio/${dados.extraction_id}/download" in html
    assert "/api/audio/batches/${batchIdAtual}/download" in html
    assert 'zipLink.download = "klango_audios.zip"' in html  # nome FIXO, pedido exato do CÉREBRO


def test_lote_de_video_continua_usando_as_rotas_de_sempre():
    """Regressão: nenhuma chamada do caminho de vídeo do lote foi redirecionada para as rotas de
    áudio."""
    html = _html()
    assert '"/api/download-batch"' in html
    assert "/api/batches/${batchIdAtual}/download" in html
    assert 'zipLink.download = "klango_lote.zip"' in html


def test_ordem_das_secoes_do_funil_frio():
    """26/10/2026 (auditoria "refinamento final", aprovação explícita do Igor): "Como funciona"
    passou a vir logo depois de "voceSeReconhece" (dores), antes de "Antes e depois" -- as demais
    seções mantiveram a ordem relativa que já tinham."""
    html = _html()
    ids_em_ordem = [
        "voceSeReconhece",
        "comoFunciona",
        "antesDepois",
        "loteNaPratica",
        "paraQuemE",
        "secaoPlanos",
        "faq",
        "ctaFinal",
    ]
    posicoes = [html.index(f'<section id="{i}"') for i in ids_em_ordem]
    assert posicoes == sorted(posicoes)


def test_caixa_nao_recebeu_codigo_com_quebra_de_linha():
    """27/10/2026 (pedido do Igor): a descrição dentro da caixa "Não recebeu o código?" (tela de
    OTP) quebrava de um jeito estranho em telas estreitas -- agora força a quebra entre "...caixa
    de entrada" e "(e spam)." com um <br> em vez de depender do wrap automático."""
    html = _html()
    assert "Verifique sua caixa de entrada<br>(e spam)." in html
    assert "Verifique sua caixa de entrada (e spam)." not in html


def test_stats_processados_escondido_para_quem_ja_logou():
    """27/10/2026 (pedido do Igor): "515 vídeos já processados" é prova social pra quem ainda não
    decidiu assinar -- pra quem já está logado a informação é redundante (o card "Meu acesso" já
    mostra o que importa), então carregarEstatisticas() nem busca nem exibe o stat nesse caso.
    Também passou a rodar só depois que a sessão é resolvida (antes rodava em paralelo, sem saber
    se havia sessão)."""
    html = _html()
    corpo = re.search(r"async function carregarEstatisticas\(\) \{(.*?)\n    \}", html, re.S).group(1)
    assert "if (sessaoEmail) return;" in corpo
    assert "carregarSessao().then(carregarEstatisticas);" in html


def test_selo_de_status_some_so_para_quem_tem_plano_pago():
    """27/10/2026 (pedido do Igor): o selo "Free · 5 de 5 restantes..." / "VIP Batch · 15 de 15
    restantes hoje" no topo da página fica redundante assim que o card "Meu acesso" aparece (mesmo
    plano, mesma informação) -- então some só nesse caso. Pra visitante e pra quem está no Free
    (sem o card "Meu acesso"), o selo continua a única forma de ver quantos usos restam."""
    html = _html()
    corpo = re.search(
        r"function atualizarVisibilidadeSecaoPlanos\(planoCode\) \{(.*?)\n    \}", html, re.S
    ).group(1)
    assert 'statusContaBadge.classList.remove("hidden")' in corpo
    assert 'statusContaBadge.classList.add("hidden")' in corpo


def test_meu_acesso_aparece_antes_do_hero():
    """27/10/2026 (pedido do Igor): assim que a pessoa loga numa conta com plano pago, o card "Meu
    acesso" deve ser a primeira coisa na página -- antes ele ficava mais abaixo (depois da vitrine
    de Planos), então o formulário do hero sempre aparecia primeiro, mesmo pra quem já tinha
    assinado. Continua "hidden" por padrão (mesma lógica de atualizarVisibilidadeSecaoPlanos()) --
    só a posição no documento mudou."""
    html = _html()
    pos_main = html.index('<main id="topo"')
    pos_area_assinatura = html.index('<section id="areaAssinatura"')
    pos_hero_titulo = html.index("Baixe vídeos sem anúncios no site e")
    assert pos_main < pos_area_assinatura < pos_hero_titulo


def test_secao_batch_mostra_formulario_real_com_cadeado_para_quem_nao_tem_lote():
    """27/10/2026 (pedido do Igor): quem não tem plano com lote (visitante ou Free) passa a ver o
    formulário de verdade da aba "Vários (lote)" -- mesmos campos, mesmo "+nome" por vídeo, mesma
    opção de áudio MP3 -- em vez da aba inteira escondida. Um cadeado (#secaoBatchBloqueado) cobre
    o formulário (#secaoBatchConteudo fica pointer-events-none/dimmed) até a pessoa assinar um
    plano com lote."""
    html = _html()
    assert 'id="secaoBatch" class="hidden relative"' in html
    assert 'id="secaoBatchConteudo"' in html
    assert 'id="secaoBatchBloqueado" class="hidden absolute inset-0' in html
    assert "Disponível nos planos Semanal, Mensal e VIP Batch." in html
    corpo = re.search(r"function atualizarVisibilidadeBatch\(plano, uso\) \{(.*?)\n    \}", html, re.S).group(1)
    assert 'secao.classList.remove("hidden")' in corpo
    assert 'secaoBatchBloqueado.classList.toggle("hidden", !bloqueado)' in corpo


def test_fila_do_atalho_nao_processa_automaticamente():
    """27/10/2026 (pedido do Igor): clicar em "Baixar vídeos"/"Extrair áudios" na fila do atalho
    ("Enviar para o KLANGO") preenchia a aba de lote E já disparava processarLoteFormato() na
    sequência, sem deixar a pessoa usar o campo "+nome" (já existente em cada item) pra renomear
    antes. Agora só preenche e rola até a aba -- quem decide quando processar é a pessoa, no botão
    #batchButton normal."""
    html = _html()
    corpo = re.search(r"async function enviarFilaParaLote\(formato\) \{(.*?)\n    \}", html, re.S).group(1)
    assert 'selecionarAbaBaixarLote("varios");' in corpo
    assert "processarLoteFormato();" not in corpo


def test_footer_com_icone_do_youtube_ao_lado_de_instagram_e_tiktok():
    """27/10/2026 (pedido do Igor): rodapé ganhou o ícone/link do YouTube, no mesmo padrão visual
    dos ícones de Instagram e TikTok já existentes (mesmas classes, mesmo tamanho)."""
    html = _html()
    assert 'href="https://www.instagram.com/klango.mp4"' in html
    assert 'href="https://www.tiktok.com/@klango.mp4"' in html
    assert 'href="https://youtube.com/@klangomp4"' in html
    assert 'aria-label="KLANGO.MP4 no YouTube"' in html
    assert "fa-brands fa-youtube" in html


# ---------------------------------------------------------------------- ETAPA B6 (Meta Pixel)


def test_aviso_de_cookies_tem_o_texto_e_os_botoes_pedidos():
    """27/10/2026 (ETAPA B6, pedido do Igor): faixa fixa de rodapé, com o texto exato pedido, link
    pra /privacidade, e os botões Aceitar/Recusar chamando responderConsentimentoCookies(). Começa
    escondida (quem decide se mostra é inicializarConsentimentoCookies(), no DOMContentLoaded) e
    com z-index abaixo dos modais (#modalAcesso e #modalPagamento), pra nunca cobrir um aberto."""
    html = _html()
    assert 'id="avisoCookies" class="hidden fixed inset-x-0 bottom-0 z-30' in html
    assert "Usamos cookies para medir nossos anúncios e melhorar o site." in html
    assert 'href="/privacidade"' in html and "Veja a Política de Privacidade." in html
    assert 'onclick="responderConsentimentoCookies(false)"' in html and ">Recusar<" in html
    assert 'onclick="responderConsentimentoCookies(true)"' in html and ">Aceitar<" in html


def test_consentimento_de_cookies_e_salvo_no_localstorage_com_a_chave_certa():
    """O valor salvo precisa ser exatamente "aceito"/"recusado" na chave "klango_consentimento"
    (pedido explícito do Igor), protegido por try/catch (não pode quebrar a página se o
    localStorage falhar -- aí a faixa só reaparece na próxima visita, nada mais)."""
    html = _html()
    assert 'CHAVE_CONSENTIMENTO_COOKIES = "klango_consentimento"' in html
    corpo_salvar = re.search(r"function salvarConsentimentoCookies\(valor\) \{(.*?)\n    \}", html, re.S).group(1)
    assert "try {" in corpo_salvar and "catch (err)" in corpo_salvar
    assert "localStorage.setItem(CHAVE_CONSENTIMENTO_COOKIES, valor)" in corpo_salvar
    corpo_ler = re.search(r"function lerConsentimentoCookies\(\) \{(.*?)\n    \}", html, re.S).group(1)
    assert "try {" in corpo_ler and "catch (err)" in corpo_ler
    assert "return null;" in corpo_ler  # sem resposta salva (ou erro) -> null, nunca quebra


def test_recusar_ou_nao_responder_nunca_carrega_o_pixel():
    """Regra central do Igor: com "recusado" ou sem resposta nenhuma, o Pixel NUNCA carrega --
    carregarMetaPixel() só é chamado em responderConsentimentoCookies(true) e, no boot da página,
    só quando o consentimento salvo já era "aceito"."""
    html = _html()
    corpo_responder = re.search(r"function responderConsentimentoCookies\(aceitou\) \{(.*?)\n    \}", html, re.S).group(1)
    assert "if (aceitou) carregarMetaPixel();" in corpo_responder
    corpo_init = re.search(r"function inicializarConsentimentoCookies\(\) \{(.*?)\n    \}", html, re.S).group(1)
    assert 'if (consentimento === "aceito")' in corpo_init
    assert "carregarMetaPixel();" in corpo_init
    assert 'mostrarAvisoCookies();' in corpo_init


def test_rastrear_e_um_no_op_seguro_quando_o_pixel_nao_existe():
    """rastrear() é a ÚNICA porta de entrada pro resto do código disparar um evento -- precisa ser
    sempre um no-op seguro se window.fbq não existir (bloqueador de anúncio, recusa, falha de
    rede): nunca pode lançar erro nem quebrar login/pagamento."""
    html = _html()
    corpo = re.search(r"function rastrear\(evento, dados, opcoes\) \{(.*?)\n    \}", html, re.S).group(1)
    assert "try {" in corpo and "catch (err)" in corpo
    assert 'if (typeof window.fbq !== "function") return;' in corpo


def test_meta_pixel_id_e_inicializacao_oficial():
    html = _html()
    assert 'META_PIXEL_ID = "1733386591088002"' in html
    assert "fbq('init', META_PIXEL_ID)" in html
    assert "fbq('track', 'PageView')" in html
    assert "connect.facebook.net/en_US/fbevents.js" in html


def test_complete_registration_dispara_no_acesso_confirmado_sem_dado_pessoal():
    """O evento precisa disparar exatamente onde "Acesso confirmado." já aparece hoje, sem nenhum
    dado pessoal (nunca e-mail/nome/telefone)."""
    html = _html()
    pos = html.index('mostrarMensagemAcesso("Acesso confirmado.", "sucesso");')
    trecho = html[pos : pos + 200]
    assert 'rastrear("CompleteRegistration");' in trecho


def test_initiate_checkout_dispara_ao_abrir_o_modal_de_pagamento_com_preco_do_mapa_front():
    """Dentro de abrirModalPagamento(plano), DEPOIS da checagem de sessão (ou seja, só quando o
    modal realmente abre) -- usa o mapa front-end PRECOS_PIXEL_POR_PLANO porque o valor real
    (amount_cents) ainda não chegou do backend nesse momento."""
    html = _html()
    assert "PRECOS_PIXEL_POR_PLANO = { weekly: 14.9, monthly: 27.9, vip_batch: 47.9 }" in html
    corpo = re.search(r"async function abrirModalPagamento\(plano\) \{(.*?)\n        const info = PLANOS\[plano\];", html, re.S).group(1)
    assert "abrirAcesso();" in corpo  # a checagem de sessão continua antes
    pos_checagem = html.index("planoPendenteAposLogin = plano;")
    pos_evento = html.index('rastrear("InitiateCheckout"')
    assert pos_checagem < pos_evento
    trecho_evento = html[pos_evento : pos_evento + 200]
    assert "value: PRECOS_PIXEL_POR_PLANO[plano]" in trecho_evento
    assert 'currency: "BRL"' in trecho_evento


def test_purchase_dispara_nos_dois_pontos_de_confirmacao_com_valor_real_e_dedupe():
    """Purchase precisa disparar nos 2 pontos existentes (cartão aprovado e Pix confirmado), com o
    valor REAL vindo do backend (paymentAtual.amount_cents), nunca do mapa front-end, e nunca
    duplicado pro mesmo pagamento (eventID + dedupe em localStorage)."""
    html = _html()
    pos_cartao = html.index('mostrarStatusPagamento("Pagamento aprovado! Confirmando seu acesso...", "sucesso");')
    assert "dispararPurchasePixel();" in html[pos_cartao : pos_cartao + 500]
    pos_pix = html.index('mostrarStatusPagamento("Pagamento confirmado! Acesso liberado.", "sucesso");')
    assert "dispararPurchasePixel();" in html[pos_pix : pos_pix + 350]

    corpo = re.search(r"function dispararPurchasePixel\(\) \{(.*?)\n    \}", html, re.S).group(1)
    assert "if (!paymentAtual || !paymentAtual.payment_id) return;" in corpo
    assert "if (jaEnviouPurchasePixel(idPagamento)) return;" in corpo
    assert "marcarPurchasePixelEnviado(idPagamento);" in corpo
    assert "value: (paymentAtual.amount_cents || 0) / 100" in corpo
    assert '{ eventID: idPagamento }' in corpo

    corpo_dedupe = re.search(r"function jaEnviouPurchasePixel\(idPagamento\) \{(.*?)\n    \}", html, re.S).group(1)
    assert "try {" in corpo_dedupe and "catch (err)" in corpo_dedupe
    assert "CHAVE_PURCHASES_PIXEL_ENVIADOS" in corpo_dedupe


def test_view_content_observa_a_secao_de_planos_uma_vez_por_visita():
    html = _html()
    corpo = re.search(r"function inicializarRastreioSecaoPlanos\(\) \{(.*?)\n    \}", html, re.S).group(1)
    assert 'getElementById("secaoPlanos")' in corpo
    assert "IntersectionObserver" in corpo
    assert 'rastrear("ViewContent", { content_name: "Planos" });' in corpo
    assert "observer.disconnect();" in corpo


def test_dom_content_loaded_inicializa_consentimento_e_rastreio_de_planos():
    html = _html()
    corpo = re.search(r'addEventListener\("DOMContentLoaded", \(\) => \{(.*?)\n    \}\);', html, re.S).group(1)
    assert "inicializarConsentimentoCookies();" in corpo
    assert "inicializarRastreioSecaoPlanos();" in corpo


def test_politica_de_privacidade_menciona_o_meta_pixel(auth_client):
    resposta = auth_client.get("/privacidade")
    assert resposta.status_code == 200
    assert "Meta Pixel" in resposta.text
    assert "aviso de cookies" in resposta.text
