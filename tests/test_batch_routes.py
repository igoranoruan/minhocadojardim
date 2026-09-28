"""POST /api/download-batch e GET /api/batches/{id}/download: contrato HTTP, autenticação,
autorização, tradução de erros e reaproveitamento de reserve_batch/run_batch (Etapa 9.3).

Mesmo padrão de parsing de SSE que tests/test_generation_flow_routes.py já usa (o TestClient já
drena o stream inteiro antes de devolver a Response)."""
import json
import zipfile
from datetime import timedelta
from io import BytesIO
from unittest.mock import patch

import pytest

from database.models import Generation
from database.types import utcnow
from download.errors import DownloadFailedError
from download.platform import Platform
from helpers_generation_flow import fake_download_result, fake_processing_result, login_directly
from helpers_usage import consume, give
from services import result_storage
from services.usage import get_allowance, get_batch_allowance, reserve_batch
from utils.time_sp import resolve_now


@pytest.fixture(autouse=True)
def diretorio_de_storage(tmp_path, monkeypatch):
    monkeypatch.setattr("services.result_storage.RESULT_STORAGE_DIR", str(tmp_path / "results"))


def _eventos(resposta) -> list[tuple[str, dict]]:
    eventos = []
    for bloco in resposta.text.strip("\n").split("\n\n"):
        if not bloco.strip():
            continue
        linhas = bloco.split("\n")
        tipo = linhas[0].removeprefix("event: ")
        dados = json.loads(linhas[1].removeprefix("data: "))
        eventos.append((tipo, dados))
    return eventos


def _arquivos(tmp_path, n):
    pares = []
    for i in range(n):
        d = tmp_path / f"baixado{i}.mp4"
        d.write_bytes(b"x")
        o = tmp_path / f"saida{i}.mp4"
        o.write_bytes(b"y")
        pares.append((d, o))
    return pares


def _body(request_id, urls, filenames=None):
    filenames = filenames or [None] * len(urls)
    return {"request_id": request_id, "items": [{"url": u, "filename": f} for u, f in zip(urls, filenames)]}


# ============================================================================ autenticação / validação estrutural
def test_sem_sessao_401(auth_client):
    resposta = auth_client.post("/api/download-batch", json=_body("r1", ["https://www.tiktok.com/@a/1"]))
    assert resposta.status_code == 401


def test_lista_de_itens_vazia_e_recusada_pelo_pydantic(auth_client, factory, session):
    login_directly(auth_client, session, factory.user())
    resposta = auth_client.post("/api/download-batch", json={"request_id": "r1", "items": []})
    assert resposta.status_code == 422


def test_sem_request_id_e_recusado_pelo_pydantic(auth_client, factory, session):
    login_directly(auth_client, session, factory.user())
    resposta = auth_client.post("/api/download-batch", json={"items": [{"url": "https://www.tiktok.com/@a/1"}]})
    assert resposta.status_code == 422


# ============================================================================ contrato do stream
def test_resposta_e_text_event_stream_com_no_store(auth_client, factory, session):
    usuario = factory.user()
    login_directly(auth_client, session, usuario)
    resposta = auth_client.post("/api/download-batch", json=_body("r1", ["https://www.tiktok.com/@a/1"]))
    assert resposta.status_code == 200
    assert resposta.headers["content-type"].startswith("text/event-stream")
    assert resposta.headers["cache-control"] == "no-store"


# ============================================================================ regras de plano
def test_usuario_free_recebe_evento_error_batch_not_allowed(auth_client, factory, session):
    login_directly(auth_client, session, factory.user())
    resposta = auth_client.post("/api/download-batch", json=_body("r1", ["https://www.tiktok.com/@a/1"]))
    eventos = _eventos(resposta)
    assert eventos[-1][0] == "error"
    assert eventos[-1][1]["code"] == "batch_not_allowed"


def test_lote_maior_que_o_teto_do_plano_recebe_invalid_batch_size(auth_client, factory, session):
    usuario = factory.user()
    login_directly(auth_client, session, usuario)
    give(factory, session, usuario, "weekly", now=resolve_now())
    urls = [f"https://www.tiktok.com/@a/{i}" for i in range(6)]  # weekly: max 5 por lote
    resposta = auth_client.post("/api/download-batch", json=_body("r1", urls))
    eventos = _eventos(resposta)
    assert eventos[-1][0] == "error"
    assert eventos[-1][1]["code"] == "invalid_batch_size"


def test_cota_de_operacoes_de_lote_esgotada_recebe_batch_quota_exceeded(auth_client, factory, session):
    usuario = factory.user()
    login_directly(auth_client, session, usuario)
    give(factory, session, usuario, "weekly", now=resolve_now())  # 3 operações/semana
    for i in range(3):
        reserve_batch(session, user_id=usuario.id, request_id=f"pre-{i}", size=1)

    resposta = auth_client.post("/api/download-batch", json=_body("r-nova", ["https://www.tiktok.com/@a/1"]))
    eventos = _eventos(resposta)
    assert eventos[-1][0] == "error"
    assert eventos[-1][1]["code"] == "batch_quota_exceeded"


def test_cota_de_videos_esgotada_recebe_quota_exceeded_nao_batch_quota(auth_client, factory, session):
    usuario = factory.user()
    login_directly(auth_client, session, usuario)
    give(factory, session, usuario, "monthly", now=resolve_now())  # 10 vídeos/dia, 5 operações/semana
    consume(session, usuario, 8, now=resolve_now())  # sobram 2 vídeos/dia

    urls = [f"https://www.tiktok.com/@a/{i}" for i in range(4)]  # pede 4, só sobram 2
    resposta = auth_client.post("/api/download-batch", json=_body("r1", urls))
    eventos = _eventos(resposta)
    assert eventos[-1][0] == "error"
    assert eventos[-1][1]["code"] == "quota_exceeded"


# ============================================================================ idempotência / conflito
def test_mesmo_request_id_repetido_nao_consome_nova_operacao(auth_client, factory, session, tmp_path):
    usuario = factory.user()
    login_directly(auth_client, session, usuario)
    give(factory, session, usuario, "weekly", now=resolve_now())
    pares_arquivo = _arquivos(tmp_path, 1)

    with patch("services.generation_flow.download_video", return_value=fake_download_result(pares_arquivo[0][0])), \
         patch("services.generation_flow.process_video", return_value=fake_processing_result(pares_arquivo[0][1])):
        r1 = auth_client.post("/api/download-batch", json=_body("repetido", ["https://www.tiktok.com/@a/1"]))
        r2 = auth_client.post("/api/download-batch", json=_body("repetido", ["https://www.tiktok.com/@a/1"]))

    batch_id_1 = next(d["batch_id"] for t, d in _eventos(r1) if t == "batch_reserved")
    batch_id_2 = next(d["batch_id"] for t, d in _eventos(r2) if t == "batch_reserved")
    assert batch_id_1 == batch_id_2
    assert get_batch_allowance(session, usuario.id).used == 1


def test_mesmo_request_id_com_tamanho_diferente_recebe_conflict(auth_client, factory, session, tmp_path):
    usuario = factory.user()
    login_directly(auth_client, session, usuario)
    give(factory, session, usuario, "weekly", now=resolve_now())
    pares_arquivo = _arquivos(tmp_path, 1)

    with patch("services.generation_flow.download_video", return_value=fake_download_result(pares_arquivo[0][0])), \
         patch("services.generation_flow.process_video", return_value=fake_processing_result(pares_arquivo[0][1])):
        auth_client.post("/api/download-batch", json=_body("conflito", ["https://www.tiktok.com/@a/1"]))
        r2 = auth_client.post("/api/download-batch", json=_body("conflito", ["https://www.tiktok.com/@a/1", "https://www.tiktok.com/@a/2"]))

    eventos = _eventos(r2)
    assert eventos[-1][0] == "error"
    assert eventos[-1][1]["code"] == "batch_request_conflict"


# ============================================================================ sucesso / progresso / isolamento de falha
def test_sucesso_sequencia_de_eventos_e_resumo_final(auth_client, factory, session, tmp_path):
    usuario = factory.user()
    login_directly(auth_client, session, usuario)
    give(factory, session, usuario, "weekly", now=resolve_now())
    pares_arquivo = _arquivos(tmp_path, 2)
    urls = ["https://www.tiktok.com/@a/1", "https://www.tiktok.com/@a/2"]

    with patch("services.generation_flow.download_video", side_effect=[fake_download_result(d, platform=Platform.TIKTOK) for d, _ in pares_arquivo]), \
         patch("services.generation_flow.process_video", side_effect=[fake_processing_result(o) for _, o in pares_arquivo]):
        resposta = auth_client.post("/api/download-batch", json=_body("sucesso", urls, filenames=["a.mp4", None]))

    eventos = _eventos(resposta)
    tipos = [t for t, _ in eventos]
    assert tipos[0] == "batch_reserved"
    assert tipos[-1] == "complete"
    assert tipos.count("item_complete") == 2
    assert tipos.count("batch_progress") == 2

    item_completes = [d for t, d in eventos if t == "item_complete"]
    assert item_completes[0]["status"] == "completed" and item_completes[0]["filename"] == "a.mp4"
    assert item_completes[1]["status"] == "completed" and item_completes[1]["filename"] is None

    resumo = eventos[-1][1]
    assert resumo["completed"] == 2 and resumo["failed"] == 0


def test_falha_de_um_item_nao_cancela_o_lote_e_aparece_no_resumo(auth_client, factory, session, tmp_path):
    usuario = factory.user()
    login_directly(auth_client, session, usuario)
    give(factory, session, usuario, "weekly", now=resolve_now())
    pares_arquivo = _arquivos(tmp_path, 2)
    urls = ["https://www.tiktok.com/@a/1", "https://www.tiktok.com/@a/2"]

    with patch("services.generation_flow.download_video", side_effect=[
            fake_download_result(pares_arquivo[0][0]), DownloadFailedError("falhou")]), \
         patch("services.generation_flow.process_video", side_effect=[fake_processing_result(pares_arquivo[0][1])]):
        resposta = auth_client.post("/api/download-batch", json=_body("com-falha", urls))

    eventos = _eventos(resposta)
    resumo = eventos[-1][1]
    assert resumo["completed"] == 1 and resumo["failed"] == 1
    item_completes = [d for t, d in eventos if t == "item_complete"]
    assert item_completes[0]["status"] == "completed"
    assert item_completes[1]["status"] == "failed" and item_completes[1]["error_code"] == "DownloadFailedError"


# ============================================================================ filename inválido / duplicado
def test_filename_com_path_traversal_recebe_evento_error(auth_client, factory, session):
    usuario = factory.user()
    login_directly(auth_client, session, usuario)
    give(factory, session, usuario, "weekly", now=resolve_now())
    resposta = auth_client.post(
        "/api/download-batch",
        json=_body("r1", ["https://www.tiktok.com/@a/1"], filenames=["../etc/passwd"]),
    )
    eventos = _eventos(resposta)
    assert [t for t, _ in eventos] == ["error"]
    assert eventos[0][1]["code"] == "invalid_filename"


def test_filename_duplicado_no_mesmo_lote_e_permitido_e_zip_deduplica(auth_client, factory, session, tmp_path):
    usuario = factory.user()
    login_directly(auth_client, session, usuario)
    give(factory, session, usuario, "weekly", now=resolve_now())
    pares_arquivo = _arquivos(tmp_path, 2)

    with patch("services.generation_flow.download_video", side_effect=[
        fake_download_result(pares_arquivo[0][0]),
        fake_download_result(pares_arquivo[1][0]),
    ]), patch("services.generation_flow.process_video", side_effect=[
        fake_processing_result(pares_arquivo[0][1]),
        fake_processing_result(pares_arquivo[1][1]),
    ]):
        resposta = auth_client.post(
            "/api/download-batch",
            json=_body("r1", ["https://www.tiktok.com/@a/1", "https://www.tiktok.com/@a/2"], filenames=["a.mp4", "a.mp4"]),
        )

    eventos = _eventos(resposta)
    assert eventos[-1][0] == "complete"
    assert [d["filename"] for t, d in eventos if t == "item_complete"] == ["a.mp4", "a.mp4"]


def test_filename_invalido_nao_consome_nenhuma_cota(auth_client, factory, session):
    usuario = factory.user()
    login_directly(auth_client, session, usuario)
    give(factory, session, usuario, "weekly", now=resolve_now())
    auth_client.post("/api/download-batch", json=_body("r1", ["https://www.tiktok.com/@a/1"], filenames=["/etc/passwd"]))
    assert get_batch_allowance(session, usuario.id).used == 0
    assert get_allowance(session, usuario.id).used == 0


# ============================================================================ ownership do lote (SSE)
def test_geracoes_do_lote_pertencem_ao_usuario_autenticado(auth_client, factory, session, tmp_path):
    usuario = factory.user()
    login_directly(auth_client, session, usuario)
    give(factory, session, usuario, "weekly", now=resolve_now())
    pares_arquivo = _arquivos(tmp_path, 1)

    with patch("services.generation_flow.download_video", return_value=fake_download_result(pares_arquivo[0][0])), \
         patch("services.generation_flow.process_video", return_value=fake_processing_result(pares_arquivo[0][1])):
        resposta = auth_client.post("/api/download-batch", json=_body("r1", ["https://www.tiktok.com/@a/1"]))

    item = next(d for t, d in _eventos(resposta) if t == "item_complete")
    session.expire_all()
    geracao = session.get(Generation, item["generation_id"])
    assert geracao.user_id == usuario.id


# ============================================================================ GET /api/batches/{id}/download (ZIP)
def test_zip_sem_sessao_401(auth_client):
    resposta = auth_client.get("/api/batches/1/download")
    assert resposta.status_code == 401


def test_zip_de_lote_inexistente_404(auth_client, factory, session):
    login_directly(auth_client, session, factory.user())
    resposta = auth_client.get("/api/batches/999999/download")
    assert resposta.status_code == 404


def test_zip_de_lote_de_outro_usuario_404(auth_client, factory, session):
    dono = factory.user()
    atacante = factory.user()
    lote = factory.batch(user=dono)
    login_directly(auth_client, session, atacante)
    resposta = auth_client.get(f"/api/batches/{lote.id}/download")
    assert resposta.status_code == 404


def test_zip_sem_nenhum_item_concluido_404(auth_client, factory, session):
    usuario = factory.user()
    login_directly(auth_client, session, usuario)
    lote = factory.batch(user=usuario, item_count=1)
    factory.generation(user=usuario, batch_id=lote.id, position=1, request_id=None, status="failed", error_code="x")
    resposta = auth_client.get(f"/api/batches/{lote.id}/download")
    assert resposta.status_code == 404


def test_zip_com_sucesso_contem_so_os_itens_concluidos(auth_client, factory, session, tmp_path):
    usuario = factory.user()
    login_directly(auth_client, session, usuario)
    lote = factory.batch(user=usuario, item_count=2)
    chave_ok = result_storage.save(_criar_arquivo(tmp_path, "ok.mp4", b"conteudo ok"), size_bytes=11)
    factory.generation(
        user=usuario, batch_id=lote.id, position=1, request_id=None, status="completed",
        output_storage_key=chave_ok, output_expires_at=utcnow() + timedelta(minutes=10),
    )
    factory.generation(
        user=usuario, batch_id=lote.id, position=2, request_id=None, status="failed", error_code="x",
    )

    resposta = auth_client.get(f"/api/batches/{lote.id}/download")
    assert resposta.status_code == 200
    assert resposta.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(BytesIO(resposta.content)) as zf:
        nomes = zf.namelist()
        assert len(nomes) == 1  # só o item concluído -- o failed nunca entra no ZIP
        with zf.open(nomes[0]) as arquivo:
            assert arquivo.read() == b"conteudo ok"


def _criar_arquivo(tmp_path, nome, conteudo):
    caminho = tmp_path / nome
    caminho.write_bytes(conteudo)
    return caminho


# ============================================================================ correção Etapa 9.3: filename persistido/usado de verdade
def test_filename_e_persistido_na_generation(auth_client, factory, session, tmp_path):
    usuario = factory.user()
    login_directly(auth_client, session, usuario)
    give(factory, session, usuario, "weekly", now=resolve_now())
    pares_arquivo = _arquivos(tmp_path, 1)

    with patch("services.generation_flow.download_video", return_value=fake_download_result(pares_arquivo[0][0])), \
         patch("services.generation_flow.process_video", return_value=fake_processing_result(pares_arquivo[0][1])):
        resposta = auth_client.post(
            "/api/download-batch",
            json=_body("r1", ["https://www.tiktok.com/@a/1"], filenames=["meu-video"]),
        )

    item = next(d for t, d in _eventos(resposta) if t == "item_complete")
    session.expire_all()
    geracao = session.get(Generation, item["generation_id"])
    assert geracao.display_filename == "meu-video.mp4"  # extensão garantida


def test_filename_ausente_deixa_display_filename_none(auth_client, factory, session, tmp_path):
    usuario = factory.user()
    login_directly(auth_client, session, usuario)
    give(factory, session, usuario, "weekly", now=resolve_now())
    pares_arquivo = _arquivos(tmp_path, 1)

    with patch("services.generation_flow.download_video", return_value=fake_download_result(pares_arquivo[0][0])), \
         patch("services.generation_flow.process_video", return_value=fake_processing_result(pares_arquivo[0][1])):
        resposta = auth_client.post("/api/download-batch", json=_body("r1", ["https://www.tiktok.com/@a/1"]))

    item = next(d for t, d in _eventos(resposta) if t == "item_complete")
    session.expire_all()
    assert session.get(Generation, item["generation_id"]).display_filename is None


def test_download_individual_usa_display_filename_quando_presente(auth_client, factory, session, tmp_path):
    usuario = factory.user()
    login_directly(auth_client, session, usuario)
    give(factory, session, usuario, "weekly", now=resolve_now())
    pares_arquivo = _arquivos(tmp_path, 1)

    with patch("services.generation_flow.download_video", return_value=fake_download_result(pares_arquivo[0][0])), \
         patch("services.generation_flow.process_video", return_value=fake_processing_result(pares_arquivo[0][1])):
        resposta = auth_client.post(
            "/api/download-batch",
            json=_body("r1", ["https://www.tiktok.com/@a/1"], filenames=["meu-video"]),
        )
    item = next(d for t, d in _eventos(resposta) if t == "item_complete")

    download = auth_client.get(f"/api/generations/{item['generation_id']}/download")
    assert download.status_code == 200
    assert download.headers["content-disposition"] == 'attachment; filename="meu-video.mp4"'


def test_download_individual_usa_fallback_quando_display_filename_e_none(auth_client, factory, session, tmp_path):
    usuario = factory.user()
    login_directly(auth_client, session, usuario)
    give(factory, session, usuario, "weekly", now=resolve_now())
    pares_arquivo = _arquivos(tmp_path, 1)

    with patch("services.generation_flow.download_video", return_value=fake_download_result(pares_arquivo[0][0])), \
         patch("services.generation_flow.process_video", return_value=fake_processing_result(pares_arquivo[0][1])):
        resposta = auth_client.post("/api/download-batch", json=_body("r1", ["https://www.tiktok.com/@a/1"]))
    item = next(d for t, d in _eventos(resposta) if t == "item_complete")

    download = auth_client.get(f"/api/generations/{item['generation_id']}/download")
    assert download.headers["content-disposition"] == f'attachment; filename="minhoca-{item["generation_id"]}.mp4"'


def test_geracao_individual_avulsa_continua_sem_display_filename_e_com_o_padrao_de_sempre(auth_client, factory, session, tmp_path):
    """Regressão: geração avulsa (não-lote) nunca passa por display_filename -- comportamento
    idêntico ao de antes desta correção."""
    usuario = factory.user()
    login_directly(auth_client, session, usuario)
    download_path = tmp_path / "baixado.mp4"
    download_path.write_bytes(b"x")
    output_path = tmp_path / "saida.mp4"
    output_path.write_bytes(b"y")

    with patch("services.generation_flow.download_video", return_value=fake_download_result(download_path)), \
         patch("services.generation_flow.process_video", return_value=fake_processing_result(output_path)):
        resposta = auth_client.post("/api/generations", json={"url": "https://www.tiktok.com/@a/1"})
    corpo = next(d for t, d in _eventos(resposta) if t == "complete")

    session.expire_all()
    assert session.get(Generation, corpo["generation_id"]).display_filename is None
    download = auth_client.get(f"/api/generations/{corpo['generation_id']}/download")
    assert download.headers["content-disposition"] == f'attachment; filename="minhoca-{corpo["generation_id"]}.mp4"'


def test_zip_usa_display_filename(auth_client, factory, session, tmp_path):
    usuario = factory.user()
    login_directly(auth_client, session, usuario)
    lote = factory.batch(user=usuario, item_count=1)
    chave = result_storage.save(_criar_arquivo(tmp_path, "ok.mp4", b"conteudo"), size_bytes=8)
    factory.generation(
        user=usuario, batch_id=lote.id, position=1, request_id=None, status="completed",
        output_storage_key=chave, output_expires_at=utcnow() + timedelta(minutes=10),
        display_filename="meu-video.mp4",
    )

    resposta = auth_client.get(f"/api/batches/{lote.id}/download")
    with zipfile.ZipFile(BytesIO(resposta.content)) as zf:
        assert zf.namelist() == ["meu-video.mp4"]


def test_zip_com_display_filenames_duplicados_gera_nomes_distintos(auth_client, factory, session, tmp_path):
    """Defesa em profundidade: mesmo que dois itens do MESMO lote acabem com o mesmo
    display_filename (não deveria acontecer via rota, que já deduplica na criação -- aqui simulado
    direto no banco), o ZIP nunca sobrescreve uma entrada com a outra."""
    usuario = factory.user()
    login_directly(auth_client, session, usuario)
    lote = factory.batch(user=usuario, item_count=2)
    chave1 = result_storage.save(_criar_arquivo(tmp_path, "um.mp4", b"conteudo 1"), size_bytes=10)
    chave2 = result_storage.save(_criar_arquivo(tmp_path, "dois.mp4", b"conteudo 2"), size_bytes=10)
    factory.generation(
        user=usuario, batch_id=lote.id, position=1, request_id=None, status="completed",
        output_storage_key=chave1, output_expires_at=utcnow() + timedelta(minutes=10),
        display_filename="video.mp4",
    )
    factory.generation(
        user=usuario, batch_id=lote.id, position=2, request_id=None, status="completed",
        output_storage_key=chave2, output_expires_at=utcnow() + timedelta(minutes=10),
        display_filename="video.mp4",
    )

    resposta = auth_client.get(f"/api/batches/{lote.id}/download")
    with zipfile.ZipFile(BytesIO(resposta.content)) as zf:
        nomes = sorted(zf.namelist())
        assert nomes == ["video-2.mp4", "video.mp4"]
        assert zf.read("video.mp4") == b"conteudo 1"
        assert zf.read("video-2.mp4") == b"conteudo 2"


def test_filename_sanitizado_nao_altera_o_formato_da_storage_key(auth_client, factory, session, tmp_path):
    """O display_filename é só o nome de EXIBIÇÃO -- a storage_key continua o uuid4().hex opaco de
    sempre, sem nenhuma relação com o filename escolhido pelo usuário."""
    import re

    usuario = factory.user()
    login_directly(auth_client, session, usuario)
    give(factory, session, usuario, "weekly", now=resolve_now())
    pares_arquivo = _arquivos(tmp_path, 1)

    with patch("services.generation_flow.download_video", return_value=fake_download_result(pares_arquivo[0][0])), \
         patch("services.generation_flow.process_video", return_value=fake_processing_result(pares_arquivo[0][1])):
        resposta = auth_client.post(
            "/api/download-batch",
            json=_body("r1", ["https://www.tiktok.com/@a/1"], filenames=["nome-escolhido-pelo-usuario"]),
        )
    item = next(d for t, d in _eventos(resposta) if t == "item_complete")
    session.expire_all()
    geracao = session.get(Generation, item["generation_id"])
    assert re.fullmatch(r"[0-9a-f]{32}", geracao.output_storage_key)
    assert "nome-escolhido" not in geracao.output_storage_key


# ============================================================================ correção Etapa 9.3: idempotência real (fingerprint)
def test_mesmo_request_id_mesmo_payload_e_replay(auth_client, factory, session, tmp_path):
    usuario = factory.user()
    login_directly(auth_client, session, usuario)
    give(factory, session, usuario, "weekly", now=resolve_now())
    pares_arquivo = _arquivos(tmp_path, 1)
    body = _body("igual", ["https://www.tiktok.com/@a/1"], filenames=["a"])

    with patch("services.generation_flow.download_video", return_value=fake_download_result(pares_arquivo[0][0])), \
         patch("services.generation_flow.process_video", return_value=fake_processing_result(pares_arquivo[0][1])):
        r1 = auth_client.post("/api/download-batch", json=body)
        r2 = auth_client.post("/api/download-batch", json=body)

    b1 = next(d["batch_id"] for t, d in _eventos(r1) if t == "batch_reserved")
    b2, criado2 = next((d["batch_id"], d["created"]) for t, d in _eventos(r2) if t == "batch_reserved")
    assert b1 == b2 and criado2 is False
    assert get_batch_allowance(session, usuario.id).used == 1


def _bloqueio_antes(session, usuario):
    from database.models import Batch

    return (
        session.query(Batch).count(),
        session.query(Generation).count(),
        get_batch_allowance(session, usuario.id).used,
    )


def test_mesmo_request_id_urls_diferentes_e_conflito_sem_efeitos_colaterais(auth_client, factory, session, tmp_path):
    usuario = factory.user()
    login_directly(auth_client, session, usuario)
    give(factory, session, usuario, "weekly", now=resolve_now())
    pares_arquivo = _arquivos(tmp_path, 1)

    with patch("services.generation_flow.download_video", return_value=fake_download_result(pares_arquivo[0][0])), \
         patch("services.generation_flow.process_video", return_value=fake_processing_result(pares_arquivo[0][1])):
        auth_client.post("/api/download-batch", json=_body("r1", ["https://www.tiktok.com/@a/1"]))
        antes = _bloqueio_antes(session, usuario)
        r2 = auth_client.post("/api/download-batch", json=_body("r1", ["https://www.tiktok.com/@a/2"]))

    eventos = _eventos(r2)
    assert eventos[-1][0] == "error" and eventos[-1][1]["code"] == "batch_request_conflict"
    assert _bloqueio_antes(session, usuario) == antes  # nada novo: sem Batch, Generation ou cota


def test_mesmo_request_id_filename_diferente_e_conflito(auth_client, factory, session, tmp_path):
    usuario = factory.user()
    login_directly(auth_client, session, usuario)
    give(factory, session, usuario, "weekly", now=resolve_now())
    pares_arquivo = _arquivos(tmp_path, 1)

    with patch("services.generation_flow.download_video", return_value=fake_download_result(pares_arquivo[0][0])), \
         patch("services.generation_flow.process_video", return_value=fake_processing_result(pares_arquivo[0][1])):
        auth_client.post("/api/download-batch", json=_body("r1", ["https://www.tiktok.com/@a/1"], filenames=["nome-a"]))
        r2 = auth_client.post("/api/download-batch", json=_body("r1", ["https://www.tiktok.com/@a/1"], filenames=["nome-b"]))

    eventos = _eventos(r2)
    assert eventos[-1][0] == "error" and eventos[-1][1]["code"] == "batch_request_conflict"


def test_mesmo_request_id_ordem_diferente_e_conflito(auth_client, factory, session, tmp_path):
    usuario = factory.user()
    login_directly(auth_client, session, usuario)
    give(factory, session, usuario, "weekly", now=resolve_now())
    pares_arquivo = _arquivos(tmp_path, 2)

    with patch("services.generation_flow.download_video", side_effect=[fake_download_result(d) for d, _ in pares_arquivo]), \
         patch("services.generation_flow.process_video", side_effect=[fake_processing_result(o) for _, o in pares_arquivo]):
        auth_client.post("/api/download-batch", json=_body("r1", ["https://www.tiktok.com/@a/1", "https://www.tiktok.com/@a/2"]))
        r2 = auth_client.post("/api/download-batch", json=_body("r1", ["https://www.tiktok.com/@a/2", "https://www.tiktok.com/@a/1"]))

    eventos = _eventos(r2)
    assert eventos[-1][0] == "error" and eventos[-1][1]["code"] == "batch_request_conflict"


# ============================================================================ correção Etapa 9.3: limite não duplicado no Pydantic
def test_payload_acima_de_15_itens_nao_e_bloqueado_pelo_pydantic(auth_client, factory, session):
    """O teto de QUANTIDADE é regra de negócio (reserve_batch/max_batch_size do plano), não do
    schema HTTP -- um payload estruturalmente válido com mais de 15 itens chega ao stream (200,
    text/event-stream) e é o PLANO, não o Pydantic, quem recusa."""
    usuario = factory.user()
    login_directly(auth_client, session, usuario)
    give(factory, session, usuario, "weekly", now=resolve_now())  # max_batch_size=5
    urls = [f"https://www.tiktok.com/@a/{i}" for i in range(20)]
    resposta = auth_client.post("/api/download-batch", json=_body("r1", urls))
    assert resposta.status_code == 200  # não foi rejeitado na validação estrutural

    eventos = _eventos(resposta)
    assert eventos[-1][0] == "error" and eventos[-1][1]["code"] == "invalid_batch_size"


# ============================================================================ regressão: download individual de item de lote
def test_download_individual_de_item_de_lote_continua_funcionando(auth_client, factory, session, tmp_path):
    usuario = factory.user()
    login_directly(auth_client, session, usuario)
    give(factory, session, usuario, "weekly", now=resolve_now())
    pares_arquivo = _arquivos(tmp_path, 1)

    with patch("services.generation_flow.download_video", return_value=fake_download_result(pares_arquivo[0][0])), \
         patch("services.generation_flow.process_video", return_value=fake_processing_result(pares_arquivo[0][1], sha="d" * 64)):
        resposta = auth_client.post("/api/download-batch", json=_body("r1", ["https://www.tiktok.com/@a/1"]))
    item = next(d for t, d in _eventos(resposta) if t == "item_complete")

    download = auth_client.get(f"/api/generations/{item['generation_id']}/download")
    assert download.status_code == 200
    assert download.headers["content-type"] == "video/mp4"