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
    # 22/10/2026 (pedido do Igor): reajuste de preço, só valor -- limites continuam iguais.
    assert "R$ 14,90" in html and "R$ 27,90" in html and "R$ 47,90" in html
    # 20/10/2026 (pedido do Igor, 2ª rodada do mesmo dia): cota diária revista pra manter Semanal <
    # Mensal < VIP Batch também no número diário (não só no teto de lote).
    assert "7 gerações/dia" in html    # Semanal
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


def test_bloco_de_cadeado_do_audio_individual_usa_a_mensagem_exata():
    html = _html()
    assert 'id="audioBloqueadoIndividual"' in html
    assert "Extrair áudio é exclusivo dos planos pagos." in html


def test_extracao_de_audio_usa_a_rota_sincrona_nova_sem_sse():
    html = _html()
    assert '"/api/audio/extract"' in html
    assert '"/api/audio/allowance"' in html
    assert "/api/audio/${dados.extraction_id}/download" in html
    assert 'placeholder="Nome do arquivo (opcional)"' not in html


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
