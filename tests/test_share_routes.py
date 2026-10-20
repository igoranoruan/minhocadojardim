"""Rotas por COOKIE do atalho "Enviar para o KLANGO" (routes/share.py, 15/10/2026): contrato HTTP
de token/fila + a MESMA proteção de Origin (CSRF) que todo o resto do site já tem -- isentar
/api/ext/share (ver tests/test_share_ext_routes.py) NÃO pode, por engano, isentar estas rotas.

Teste obrigatório do CÉREBRO (ajuste de aprovação da Etapa 2): confirmar que DELETE /api/share/queue
e POST /api/share/queue/send-to-batch continuam recebendo 403 sem Origin válido -- ver a seção
final deste arquivo, espelhando tests/test_auth_origin.py."""
import pytest
from fastapi.testclient import TestClient

from helpers_generation_flow import login_directly
from helpers_usage import give
from main import app
from services.share_queue import _attempts_by_user, add_to_queue, list_queue

BASE_URL = "http://localhost:8000"


@pytest.fixture(autouse=True)
def _limpa_rate_limit_em_memoria():
    _attempts_by_user.clear()
    yield
    _attempts_by_user.clear()


# ------------------------------------------------------------------ token
def test_token_nao_existe_antes_de_gerar(auth_client, session, factory):
    login_directly(auth_client, session, factory.user())
    resposta = auth_client.get("/api/share/token")
    assert resposta.status_code == 200
    assert resposta.json() == {"exists": False, "created_at": None, "last_used_at": None}


def test_gerar_consultar_e_revogar_token(auth_client, session, factory):
    login_directly(auth_client, session, factory.user())

    gerado = auth_client.post("/api/share/token")
    assert gerado.status_code == 200
    token = gerado.json()["token"]
    assert isinstance(token, str) and len(token) > 20

    status = auth_client.get("/api/share/token")
    assert status.json()["exists"] is True
    assert status.json()["last_used_at"] is None

    revogado = auth_client.delete("/api/share/token")
    assert revogado.status_code == 204

    status_final = auth_client.get("/api/share/token")
    assert status_final.json()["exists"] is False


def test_gerar_novo_token_invalida_o_anterior(auth_client, session, factory):
    login_directly(auth_client, session, factory.user())
    primeiro = auth_client.post("/api/share/token").json()["token"]
    segundo = auth_client.post("/api/share/token").json()["token"]
    assert primeiro != segundo


def test_rotas_de_token_exigem_sessao(engine):
    cliente = TestClient(app, base_url=BASE_URL)
    cliente.headers.update({"Origin": BASE_URL})
    assert cliente.get("/api/share/token").status_code == 401
    assert cliente.post("/api/share/token").status_code == 401
    assert cliente.delete("/api/share/token").status_code == 401


# ------------------------------------------------------------------ fila
def test_post_queue_adiciona_por_cookie(auth_client, session, factory):
    """POST /api/share/queue (17/10/2026, suporte a Android via PWA) -- usada pelo frontend
    depois do login, pra consumir um link que ficou pendente em localStorage (ver
    routes/share_target.py). Mesma lógica de POST /api/ext/share, só que por cookie."""
    usuario = factory.user()
    login_directly(auth_client, session, usuario)

    resposta = auth_client.post("/api/share/queue", json={"url": "https://www.tiktok.com/@a/video/1"})
    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["added"] is True
    assert corpo["pending_count"] == 1
    assert len(list_queue(session, usuario)) == 1


def test_post_queue_extrai_link_de_text(auth_client, session, factory):
    usuario = factory.user()
    login_directly(auth_client, session, usuario)

    resposta = auth_client.post("/api/share/queue", json={"text": "olha: https://www.tiktok.com/@a/video/1"})
    assert resposta.status_code == 200
    assert len(list_queue(session, usuario)) == 1


def test_listar_remover_e_limpar_fila(auth_client, session, factory):
    usuario = factory.user()
    login_directly(auth_client, session, usuario)

    add_to_queue(session, usuario, url="https://www.tiktok.com/@a/video/1", text=None)
    add_to_queue(session, usuario, url="https://www.tiktok.com/@a/video/2", text=None)

    listagem = auth_client.get("/api/share/queue")
    assert listagem.status_code == 200
    itens = listagem.json()["items"]
    assert len(itens) == 2

    primeiro_id = itens[0]["id"]
    removido = auth_client.delete(f"/api/share/queue/{primeiro_id}")
    assert removido.status_code == 204
    assert len(auth_client.get("/api/share/queue").json()["items"]) == 1

    limpo = auth_client.delete("/api/share/queue")
    assert limpo.status_code == 204
    assert auth_client.get("/api/share/queue").json()["items"] == []


def test_remover_item_de_outro_usuario_nao_apaga_nada(auth_client, session, factory):
    dono = factory.user()
    outro = factory.user()
    add_to_queue(session, dono, url="https://www.tiktok.com/@a/video/1", text=None)
    add_to_queue(session, dono, url="https://www.tiktok.com/@a/video/2", text=None)
    item = list_queue(session, dono)[0]

    login_directly(auth_client, session, outro)
    resposta = auth_client.delete(f"/api/share/queue/{item.id}")
    assert resposta.status_code == 204  # idempotente -- não revela se o item existe ou é de outro dono

    login_directly(auth_client, session, dono)
    assert len(auth_client.get("/api/share/queue").json()["items"]) == 2


def test_send_to_batch_preenche_ate_o_teto_do_plano(auth_client, session, factory):
    from database.types import utcnow

    usuario = factory.user()
    give(factory, session, usuario, "weekly", now=utcnow())  # max_batch_size = 10 (20/10/2026)
    login_directly(auth_client, session, usuario)
    for i in range(11):
        add_to_queue(session, usuario, url=f"https://www.tiktok.com/@a/video/{i}", text=None)

    resposta = auth_client.post("/api/share/queue/send-to-batch")
    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["sent_count"] == 10
    assert corpo["max_batch_size"] == 10
    assert corpo["remaining_pending"] == 1
    assert len(corpo["urls"]) == 10


# 17/10/2026 (aprovação do CÉREBRO, ajuste #2 -- botão "Extrair áudios"): `?formato=audio` nunca
# pode separar (e marcar 'consumed') mais itens da fila do que o saldo diário de áudio restante
# aceita -- reserve_audio_batch é tudo-ou-nada, então sem essa trava o excedente seria perdido da
# fila sem nenhum áudio ter sido extraído. Ver services/share_queue.py::send_to_batch (max_items) e
# tests/test_share_queue_service.py (testes de max_items na camada de serviço).
def test_send_to_batch_formato_audio_respeita_o_saldo_diario_e_mantem_o_excedente_na_fila(
    auth_client, session, factory,
):
    from config import AUDIO_EXTRACTIONS_PER_DAY
    from database.types import utcnow
    from services.audio_usage import complete_audio_extraction, reserve_audio_extraction

    usuario = factory.user()
    give(factory, session, usuario, "vip_batch", now=utcnow())  # max_batch_size = 15, sem lote áudio isento
    login_directly(auth_client, session, usuario)

    # Consome o saldo diário de áudio até sobrarem só 3 extrações hoje (cada reserva precisa ser
    # completada antes da próxima -- "uma operação de áudio por vez", mesmo padrão de
    # tests/test_audio_usage_batch.py::test_lote_e_limitado_pelo_saldo_diario_restante_nao_so_pelo_plano).
    for i in range(AUDIO_EXTRACTIONS_PER_DAY - 3):
        reserva = reserve_audio_extraction(session, user_id=usuario.id, request_id=f"avulsa-{i}", now=utcnow())
        complete_audio_extraction(session, reserva.extraction_id, now=utcnow())

    for i in range(10):
        add_to_queue(session, usuario, url=f"https://www.tiktok.com/@a/video/{i}", text=None)

    resposta = auth_client.post("/api/share/queue/send-to-batch?formato=audio")
    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["sent_count"] == 3  # travado pelo saldo diário, não pelo teto do plano (15)
    assert corpo["remaining_pending"] == 7

    restantes = auth_client.get("/api/share/queue").json()["items"]
    assert len(restantes) == 7  # o excedente NUNCA é perdido -- continua pendente na fila


def test_send_to_batch_formato_video_continua_usando_so_o_teto_do_plano(auth_client, session, factory):
    """Regressão: o botão de VÍDEO (formato padrão) nunca deve ser afetado pelo saldo de áudio."""
    from database.types import utcnow

    usuario = factory.user()
    give(factory, session, usuario, "weekly", now=utcnow())  # max_batch_size = 10 (20/10/2026)
    login_directly(auth_client, session, usuario)
    for i in range(11):
        add_to_queue(session, usuario, url=f"https://www.tiktok.com/@a/video/{i}", text=None)

    resposta = auth_client.post("/api/share/queue/send-to-batch?formato=video")
    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["sent_count"] == 10
    assert corpo["remaining_pending"] == 1


def test_rotas_de_fila_exigem_sessao(engine):
    cliente = TestClient(app, base_url=BASE_URL)
    cliente.headers.update({"Origin": BASE_URL})
    assert cliente.get("/api/share/queue").status_code == 401
    assert cliente.post("/api/share/queue", json={"url": "https://www.tiktok.com/@a/video/1"}).status_code == 401
    assert cliente.delete("/api/share/queue/1").status_code == 401
    assert cliente.delete("/api/share/queue").status_code == 401
    assert cliente.post("/api/share/queue/send-to-batch").status_code == 401


# ------------------------------------------------------------------ CSRF continua protegendo estas rotas
# Mesmo padrão de tests/test_auth_origin.py: sem Origin/Referer válido, o OriginProtectionMiddleware
# barra a requisição ANTES de chegar na rota -- nem precisa de sessão/login para provar isso.
def test_post_queue_sem_origin_valido_recebe_403(engine):
    cliente = TestClient(app, base_url=BASE_URL)  # sem Origin
    resposta = cliente.post("/api/share/queue", json={"url": "https://www.tiktok.com/@a/video/1"})
    assert resposta.status_code == 403
    assert resposta.json()["code"] == "invalid_origin"


def test_delete_queue_sem_origin_valido_recebe_403(engine):
    cliente = TestClient(app, base_url=BASE_URL)  # sem Origin
    resposta = cliente.delete("/api/share/queue")
    assert resposta.status_code == 403
    assert resposta.json()["code"] == "invalid_origin"


def test_delete_queue_item_sem_origin_valido_recebe_403(engine):
    cliente = TestClient(app, base_url=BASE_URL)
    resposta = cliente.delete("/api/share/queue/1")
    assert resposta.status_code == 403
    assert resposta.json()["code"] == "invalid_origin"


def test_send_to_batch_sem_origin_valido_recebe_403(engine):
    cliente = TestClient(app, base_url=BASE_URL)
    resposta = cliente.post("/api/share/queue/send-to-batch")
    assert resposta.status_code == 403
    assert resposta.json()["code"] == "invalid_origin"


def test_post_token_sem_origin_valido_recebe_403(engine):
    cliente = TestClient(app, base_url=BASE_URL)
    resposta = cliente.post("/api/share/token")
    assert resposta.status_code == 403
    assert resposta.json()["code"] == "invalid_origin"


def test_delete_token_sem_origin_valido_recebe_403(engine):
    cliente = TestClient(app, base_url=BASE_URL)
    resposta = cliente.delete("/api/share/token")
    assert resposta.status_code == 403
    assert resposta.json()["code"] == "invalid_origin"
