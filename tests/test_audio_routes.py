"""routes/audio.py: contrato HTTP da EXTRAÇÃO DE ÁUDIO avulsa -- Parte A, autorização do CÉREBRO,
11/10/2026. Mesmo padrão de tests/test_generation_flow_routes.py, mas rota SÍNCRONA (sem SSE):
cada cenário de negócio vira um status HTTP real, nunca um evento dentro de um stream 200."""
from unittest.mock import patch

from database.models import AudioExtraction, Generation
from download.platform import Platform
from helpers_audio import fake_audio_download_result, fake_audio_processing_result
from helpers_generation_flow import login_directly
from processor.errors import NoAudioStreamError
from services.audio_usage import complete_audio_extraction, reserve_audio_extraction
from utils.time_sp import now_sp


def _entitlement_pago(factory, usuario, plan_code="weekly"):
    """Mesmo padrão de tests/test_generation_flow_routes.py::_entitlement_semanal, usando o
    relógio real (now_sp()) -- a rota de áudio nunca recebe `now` injetado (não existe esse
    parâmetro na API HTTP), então o entitlement de teste precisa valer AGORA de verdade."""
    now = now_sp()
    payment = factory.payment(usuario, plan_code=plan_code, status="approved")
    return factory.entitlement(
        payment, plan_code=plan_code, duration_days=30, starts_at=now,
        expires_at=now.replace(year=now.year + 1),
    )


def _arquivos(diretorio):
    baixado = diretorio / "fonte.m4a"
    baixado.write_bytes(b"fonte de audio")
    saida = diretorio / "saida.mp3"
    saida.write_bytes(b"mp3 processado")
    return baixado, saida


# ============================================================================ autenticação / plano pago
def test_sem_sessao_e_recusada(auth_client):
    """Diferente das rotas de vídeo (Free anônimo), /api/audio/* usa get_current_user: sem sessão
    nenhuma, é recusada (401) -- "código equivalente do projeto" para bloquear quem não está
    logado, nunca 200."""
    resposta = auth_client.get("/api/audio/allowance")
    assert resposta.status_code == 401


def test_logado_sem_plano_pago_e_bloqueado_com_403(auth_client, factory, session):
    usuario = factory.user()  # sem nenhum entitlement -> plano efetivo Free
    login_directly(auth_client, session, usuario)

    resposta = auth_client.get("/api/audio/allowance")
    assert resposta.status_code == 403
    corpo = resposta.json()
    assert corpo["detail"] == "Extrair áudio é exclusivo dos planos pagos."
    assert corpo["code"] == "audio_not_allowed"


def test_extract_para_free_tambem_e_bloqueado_com_403_e_nao_grava_nada(auth_client, factory, session):
    usuario = factory.user()
    login_directly(auth_client, session, usuario)

    resposta = auth_client.post("/api/audio/extract", json={"url": "https://www.tiktok.com/@a/video/1"})
    assert resposta.status_code == 403
    assert resposta.json()["detail"] == "Extrair áudio é exclusivo dos planos pagos."
    session.expire_all()
    assert session.query(AudioExtraction).count() == 0


# ============================================================================ sucesso
def test_extracao_com_sucesso_devolve_200_e_atualiza_o_saldo(auth_client, factory, session, same_fs_tmp_path):
    usuario = factory.user()
    login_directly(auth_client, session, usuario)
    _entitlement_pago(factory, usuario)
    baixado, saida = _arquivos(same_fs_tmp_path)

    with patch(
        "services.audio_flow.download_audio_source",
        return_value=fake_audio_download_result(baixado, platform=Platform.TIKTOK),
    ), patch(
        "services.audio_flow.process_audio",
        return_value=fake_audio_processing_result(saida, sha="d" * 64, duration=7.5, size=555),
    ):
        resposta = auth_client.post("/api/audio/extract", json={"url": "https://www.tiktok.com/@a/video/1"})

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["status"] == "completed"
    assert corpo["platform"] == "tiktok"
    assert corpo["output_sha256"] == "d" * 64
    assert corpo["size_bytes"] == 555
    assert corpo["used"] == 1
    assert corpo["remaining"] == 14
    assert corpo["limit"] == 15

    # nunca consome geração (pedido explícito do CÉREBRO)
    assert session.query(Generation).count() == 0


def test_saldo_via_allowance_reflete_extracoes_ja_feitas(auth_client, factory, session, same_fs_tmp_path):
    usuario = factory.user()
    login_directly(auth_client, session, usuario)
    _entitlement_pago(factory, usuario)
    baixado, saida = _arquivos(same_fs_tmp_path)

    with patch("services.audio_flow.download_audio_source", return_value=fake_audio_download_result(baixado)), \
         patch("services.audio_flow.process_audio", return_value=fake_audio_processing_result(saida)):
        auth_client.post("/api/audio/extract", json={"url": "https://www.tiktok.com/@a/video/1"})

    resposta = auth_client.get("/api/audio/allowance")
    assert resposta.status_code == 200
    assert resposta.json() == {"used": 1, "limit": 15, "remaining": 14, "unlimited": False}


# ============================================================================ limite diário / concorrência (HTTP real, nunca SSE)
def test_limite_diario_esgotado_devolve_429_com_mensagem_exata(auth_client, factory, session):
    usuario = factory.user()
    login_directly(auth_client, session, usuario)
    _entitlement_pago(factory, usuario)

    agora = now_sp()
    for i in range(15):
        reserva = reserve_audio_extraction(session, user_id=usuario.id, request_id=f"pre-{i}", now=agora)
        complete_audio_extraction(session, reserva.extraction_id, now=agora)

    resposta = auth_client.post("/api/audio/extract", json={"url": "https://www.tiktok.com/@a/video/1"})
    assert resposta.status_code == 429
    corpo = resposta.json()
    assert corpo["detail"] == "Você usou suas 15 extrações de hoje. Amanhã libera de novo."
    assert corpo["code"] == "audio_quota_exceeded"


def test_operacao_de_audio_em_andamento_devolve_409(auth_client, factory, session):
    usuario = factory.user()
    login_directly(auth_client, session, usuario)
    _entitlement_pago(factory, usuario)
    reserve_audio_extraction(session, user_id=usuario.id, request_id="ja-em-andamento", now=now_sp())

    resposta = auth_client.post("/api/audio/extract", json={"url": "https://www.tiktok.com/@a/video/1"})
    assert resposta.status_code == 409
    assert resposta.json()["code"] == "audio_concurrent_operation"


# ============================================================================ vídeo sem áudio / falha não conta
def test_video_sem_audio_devolve_mensagem_exata_e_nao_consome_o_limite(auth_client, factory, session, same_fs_tmp_path):
    usuario = factory.user()
    login_directly(auth_client, session, usuario)
    _entitlement_pago(factory, usuario)
    baixado, _ = _arquivos(same_fs_tmp_path)

    with patch("services.audio_flow.download_audio_source", return_value=fake_audio_download_result(baixado)), \
         patch("services.audio_flow.process_audio", side_effect=NoAudioStreamError("sem stream de áudio")):
        resposta = auth_client.post("/api/audio/extract", json={"url": "https://www.tiktok.com/@a/video/1"})

    # NoAudioStreamError é um ProcessorError -- o handler é o MESMO, global, reaproveitado da
    # rota de vídeo (main.py: app.add_exception_handler(ProcessorError, generations.
    # processor_error_handler), que sempre responde 422 de propósito -- nunca 400. routes/audio.py
    # nunca registrou um handler próprio pra sobrescrever isso, então o contrato real da API aqui
    # é 422 (o frontend não diferencia por status nesse caso, só mostra `detail`).
    assert resposta.status_code == 422
    assert resposta.json()["detail"] == "Esse vídeo não tem áudio."

    saldo = auth_client.get("/api/audio/allowance").json()
    assert saldo == {"used": 0, "limit": 15, "remaining": 15, "unlimited": False}  # falha não conta


def test_erro_de_download_nao_vaza_detalhe_tecnico(auth_client, factory, session):
    usuario = factory.user()
    login_directly(auth_client, session, usuario)
    _entitlement_pago(factory, usuario)

    from download.errors import DownloadFailedError

    with patch("services.audio_flow.download_audio_source", side_effect=DownloadFailedError("stderr sensível: /caminho/interno")):
        resposta = auth_client.post("/api/audio/extract", json={"url": "https://www.tiktok.com/@a/video/1"})

    assert "/caminho/interno" not in resposta.text
    assert set(resposta.json()) == {"detail", "code"}


# ============================================================================ download do MP3
def test_download_do_mp3_tem_content_type_e_nome_corretos(auth_client, factory, session, same_fs_tmp_path):
    usuario = factory.user()
    login_directly(auth_client, session, usuario)
    _entitlement_pago(factory, usuario)
    baixado, saida = _arquivos(same_fs_tmp_path)

    with patch("services.audio_flow.download_audio_source", return_value=fake_audio_download_result(baixado)), \
         patch("services.audio_flow.process_audio", return_value=fake_audio_processing_result(saida)):
        criada = auth_client.post("/api/audio/extract", json={"url": "https://www.tiktok.com/@a/video/1"})
    extraction_id = criada.json()["extraction_id"]

    resposta = auth_client.get(f"/api/audio/{extraction_id}/download")
    assert resposta.status_code == 200
    assert resposta.headers["content-type"] == "audio/mpeg"
    assert "audio_klango.mp3" in resposta.headers["content-disposition"]
    assert resposta.headers["cache-control"] == "no-store"
    assert resposta.content == b"mp3 processado"


def test_download_de_extracao_de_outro_usuario_devolve_404_generico(auth_client, factory, session, same_fs_tmp_path):
    dono = factory.user()
    outro = factory.user()
    _entitlement_pago(factory, dono)
    _entitlement_pago(factory, outro)
    baixado, saida = _arquivos(same_fs_tmp_path)

    login_directly(auth_client, session, dono)
    with patch("services.audio_flow.download_audio_source", return_value=fake_audio_download_result(baixado)), \
         patch("services.audio_flow.process_audio", return_value=fake_audio_processing_result(saida)):
        criada = auth_client.post("/api/audio/extract", json={"url": "https://www.tiktok.com/@a/video/1"})
    extraction_id = criada.json()["extraction_id"]

    login_directly(auth_client, session, outro)
    resposta = auth_client.get(f"/api/audio/{extraction_id}/download")
    assert resposta.status_code == 404


def test_download_de_extracao_inexistente_devolve_404(auth_client, factory, session):
    usuario = factory.user()
    login_directly(auth_client, session, usuario)
    _entitlement_pago(factory, usuario)
    resposta = auth_client.get("/api/audio/999999/download")
    assert resposta.status_code == 404


# ============================================================================ Parte 1 -- link de página de áudio (autorização do CÉREBRO, 11/10/2026)
def test_link_de_pagina_de_audio_do_tiktok_devolve_mensagem_amigavel_sem_consumir_nada(
    auth_client, factory, session,
):
    """TikTok `/music/...` -- mensagem EXATA pedida pelo CÉREBRO, 422 (mesmo handler de
    DownloadError já existente, nenhum handler novo), NENHUMA extração gravada, download_audio_source
    NUNCA chamado (a checagem é a primeira coisa do fluxo, antes de qualquer reserva)."""
    usuario = factory.user()
    login_directly(auth_client, session, usuario)
    _entitlement_pago(factory, usuario)

    with patch("services.audio_flow.download_audio_source") as download_mock:
        resposta = auth_client.post(
            "/api/audio/extract",
            json={"url": "https://www.tiktok.com/music/Build-a-Btch-6956990112127585029"},
        )

    assert resposta.status_code == 422
    assert resposta.json()["detail"] == (
        "Esse é o link da página do áudio. Abra um dos vídeos que usam esse som, "
        "toque em Compartilhar → Copiar link e cole aqui."
    )
    download_mock.assert_not_called()
    session.expire_all()
    assert session.query(AudioExtraction).count() == 0
    saldo = auth_client.get("/api/audio/allowance").json()
    assert saldo == {"used": 0, "limit": 15, "remaining": 15, "unlimited": False}


def test_link_de_pagina_de_audio_do_instagram_devolve_mensagem_amigavel(auth_client, factory, session):
    """Instagram `/reels/audio/...` e `/audio/...` -- mesmo comportamento do TikTok acima."""
    usuario = factory.user()
    login_directly(auth_client, session, usuario)
    _entitlement_pago(factory, usuario)

    for url in (
        "https://www.instagram.com/reels/audio/123456789/",
        "https://www.instagram.com/audio/123456789/",
    ):
        with patch("services.audio_flow.download_audio_source") as download_mock:
            resposta = auth_client.post("/api/audio/extract", json={"url": url})
        assert resposta.status_code == 422
        assert resposta.json()["detail"] == (
            "Esse é o link da página do áudio. Abra um dos vídeos que usam esse som, "
            "toque em Compartilhar → Copiar link e cole aqui."
        )
        download_mock.assert_not_called()

    session.expire_all()
    assert session.query(AudioExtraction).count() == 0


def test_link_de_pagina_de_audio_e_bloqueado_mesmo_para_free(auth_client, factory, session):
    """A checagem de link de página de áudio vem ANTES da checagem de plano pago -- mas, como o
    Free já é recusado de qualquer forma (403), o que importa aqui é que NENHUM dos dois caminhos
    grava linha nenhuma. Documentado por clareza (não é um requisito nomeado, mas evita um
    comportamento surpreendente: um Free vendo 422 'página do áudio' em vez de 403 'plano pago').
    """
    usuario = factory.user()
    login_directly(auth_client, session, usuario)  # sem entitlement -> plano efetivo Free

    resposta = auth_client.post(
        "/api/audio/extract",
        json={"url": "https://www.tiktok.com/music/Build-a-Btch-6956990112127585029"},
    )
    # A mensagem amigável é devolvida ANTES da checagem de plano (nunca precisa de plano pago para
    # saber que um link de página de áudio não serve pra nada) -- 422, nunca 403, mesma mensagem
    # exata de sempre.
    assert resposta.status_code == 422
    assert resposta.json()["detail"] == (
        "Esse é o link da página do áudio. Abra um dos vídeos que usam esse som, "
        "toque em Compartilhar → Copiar link e cole aqui."
    )
