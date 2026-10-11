"""POST /api/audio/batch e GET /api/audio/batches/{id}/download: contrato HTTP, autenticação,
autorização, tradução de erros e reaproveitamento de reserve_audio_batch/run_audio_batch
(Parte B, autorização do CÉREBRO, 11/10/2026).

Mesmo padrão de tests/test_batch_routes.py (lote de vídeo) e tests/test_audio_routes.py (avulsa)."""
import json
import zipfile
from datetime import timedelta
from io import BytesIO
from unittest.mock import patch

import pytest

from database.models import AudioBatch, AudioExtraction, Batch, Generation
from database.types import utcnow
from download.errors import DownloadFailedError
from download.platform import Platform
from helpers_audio import fake_audio_download_result, fake_audio_processing_result
from helpers_generation_flow import login_directly
from helpers_usage import give
from services import result_storage
from services.audio_usage import complete_audio_extraction, get_audio_allowance, reserve_audio_batch
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
        d = tmp_path / f"fonte{i}.m4a"
        d.write_bytes(b"x")
        o = tmp_path / f"saida{i}.mp3"
        o.write_bytes(b"y")
        pares.append((d, o))
    return pares


def _body(request_id, urls, filenames=None):
    filenames = filenames or [None] * len(urls)
    return {"request_id": request_id, "items": [{"url": u, "filename": f} for u, f in zip(urls, filenames)]}


def _criar_arquivo(tmp_path, nome, conteudo):
    caminho = tmp_path / nome
    caminho.write_bytes(conteudo)
    return caminho


# ============================================================================ autenticação / validação estrutural
def test_sem_sessao_401(auth_client):
    resposta = auth_client.post("/api/audio/batch", json=_body("r1", ["https://www.tiktok.com/@a/1"]))
    assert resposta.status_code == 401


def test_lista_de_itens_vazia_e_recusada_pelo_pydantic(auth_client, factory, session):
    login_directly(auth_client, session, factory.user())
    resposta = auth_client.post("/api/audio/batch", json={"request_id": "r1", "items": []})
    assert resposta.status_code == 422


# ============================================================================ contrato do stream
def test_resposta_e_text_event_stream_com_no_store(auth_client, factory, session):
    login_directly(auth_client, session, factory.user())
    resposta = auth_client.post("/api/audio/batch", json=_body("r1", ["https://www.tiktok.com/@a/1"]))
    assert resposta.status_code == 200
    assert resposta.headers["content-type"].startswith("text/event-stream")
    assert resposta.headers["cache-control"] == "no-store"


# ============================================================================ regras de plano
def test_usuario_free_recebe_evento_error_audio_not_allowed(auth_client, factory, session):
    login_directly(auth_client, session, factory.user())
    resposta = auth_client.post("/api/audio/batch", json=_body("r1", ["https://www.tiktok.com/@a/1"]))
    eventos = _eventos(resposta)
    assert eventos[-1][0] == "error"
    assert eventos[-1][1]["code"] == "audio_not_allowed"
    assert eventos[-1][1]["detail"] == "Extrair áudio é exclusivo dos planos pagos."


def test_lote_maior_que_o_teto_do_plano_recebe_invalid_audio_batch_size(auth_client, factory, session):
    usuario = factory.user()
    login_directly(auth_client, session, usuario)
    give(factory, session, usuario, "weekly", now=resolve_now())  # weekly: max 5 por lote
    urls = [f"https://www.tiktok.com/@a/{i}" for i in range(6)]
    resposta = auth_client.post("/api/audio/batch", json=_body("r1", urls))
    eventos = _eventos(resposta)
    assert eventos[-1][0] == "error"
    assert eventos[-1][1]["code"] == "invalid_audio_batch_size"


def test_lote_maior_que_o_saldo_diario_restante_recebe_audio_quota_exceeded(auth_client, factory, session):
    """Mesmo dentro do teto do PLANO (vip_batch aceita até 15/operação), se já restam menos
    extrações diárias do que o tamanho pedido, o lote é recusado -- min(max_batch_size, saldo
    restante), pedido EXATO do CÉREBRO."""
    usuario = factory.user()
    login_directly(auth_client, session, usuario)
    give(factory, session, usuario, "vip_batch", now=resolve_now())
    reserva = reserve_audio_batch(session, user_id=usuario.id, request_id="pre-consumo", size=12)
    for eid in reserva.extraction_ids:
        complete_audio_extraction(session, eid)
    # restam 3 extrações hoje; pedir 5 deve ser recusado como excesso de cota diária.
    resposta = auth_client.post("/api/audio/batch", json=_body("r-novo", [f"https://www.tiktok.com/@a/{i}" for i in range(5)]))
    eventos = _eventos(resposta)
    assert eventos[-1][0] == "error"
    assert eventos[-1][1]["code"] == "audio_quota_exceeded"
    assert eventos[-1][1]["detail"] == "Você usou suas 15 extrações de hoje. Amanhã libera de novo."


def test_lote_de_video_em_andamento_nunca_bloqueia_lote_de_audio(auth_client, factory, session, tmp_path):
    """Confirma o ISOLAMENTO entre as duas camadas: um lote de VÍDEO em andamento (tabela
    `batches`) NUNCA bloqueia um lote de ÁUDIO (tabela `audio_batches`), e vice-versa -- "uma
    operação por vez" é uma regra só DENTRO de cada camada, nunca entre elas."""
    from services.usage import reserve_batch

    usuario = factory.user()
    login_directly(auth_client, session, usuario)
    give(factory, session, usuario, "weekly", now=resolve_now())
    reserve_batch(session, user_id=usuario.id, request_id="video-em-andamento", size=1)

    resposta = auth_client.post("/api/audio/batch", json=_body("r1", ["https://www.tiktok.com/@a/1"]))
    eventos = _eventos(resposta)
    assert eventos[0][0] == "batch_reserved"  # não foi bloqueado pelo lote de vídeo


# ============================================================================ sucesso / progresso / isolamento de falha
def test_sucesso_sequencia_de_eventos_e_resumo_final(auth_client, factory, session, tmp_path):
    usuario = factory.user()
    login_directly(auth_client, session, usuario)
    give(factory, session, usuario, "weekly", now=resolve_now())
    pares_arquivo = _arquivos(tmp_path, 2)
    urls = ["https://www.tiktok.com/@a/1", "https://www.tiktok.com/@a/2"]

    with patch("services.audio_flow.download_audio_source", side_effect=[fake_audio_download_result(d, platform=Platform.TIKTOK) for d, _ in pares_arquivo]), \
         patch("services.audio_flow.process_audio", side_effect=[fake_audio_processing_result(o) for _, o in pares_arquivo]):
        resposta = auth_client.post("/api/audio/batch", json=_body("sucesso", urls, filenames=["a", None]))

    eventos = _eventos(resposta)
    tipos = [t for t, _ in eventos]
    assert tipos[0] == "batch_reserved"
    assert tipos[-1] == "complete"
    assert tipos.count("item_complete") == 2
    assert tipos.count("batch_progress") == 2

    item_completes = [d for t, d in eventos if t == "item_complete"]
    assert item_completes[0]["status"] == "completed" and item_completes[0]["filename"] == "a.mp3"
    assert item_completes[1]["status"] == "completed" and item_completes[1]["filename"] is None

    resumo = eventos[-1][1]
    assert resumo["completed"] == 2 and resumo["failed"] == 0


def test_falha_de_um_item_nao_cancela_o_lote_e_aparece_no_resumo(auth_client, factory, session, tmp_path):
    usuario = factory.user()
    login_directly(auth_client, session, usuario)
    give(factory, session, usuario, "weekly", now=resolve_now())
    pares_arquivo = _arquivos(tmp_path, 2)
    urls = ["https://www.tiktok.com/@a/1", "https://www.tiktok.com/@a/2"]

    with patch("services.audio_flow.download_audio_source", side_effect=[
            fake_audio_download_result(pares_arquivo[0][0]), DownloadFailedError("falhou")]), \
         patch("services.audio_flow.process_audio", side_effect=[fake_audio_processing_result(pares_arquivo[0][1])]):
        resposta = auth_client.post("/api/audio/batch", json=_body("com-falha", urls))

    eventos = _eventos(resposta)
    resumo = eventos[-1][1]
    assert resumo["completed"] == 1 and resumo["failed"] == 1
    item_completes = [d for t, d in eventos if t == "item_complete"]
    assert item_completes[0]["status"] == "completed"
    assert item_completes[1]["status"] == "failed" and item_completes[1]["error_code"] == "DownloadFailedError"


def test_falha_do_item_nao_consome_o_limite_diario(auth_client, factory, session, tmp_path):
    usuario = factory.user()
    login_directly(auth_client, session, usuario)
    give(factory, session, usuario, "weekly", now=resolve_now())
    pares_arquivo = _arquivos(tmp_path, 1)

    with patch("services.audio_flow.download_audio_source", side_effect=[fake_audio_download_result(pares_arquivo[0][0]), DownloadFailedError("x")]), \
         patch("services.audio_flow.process_audio", side_effect=[fake_audio_processing_result(pares_arquivo[0][1])]):
        auth_client.post("/api/audio/batch", json=_body("r1", ["https://www.tiktok.com/@a/1", "https://www.tiktok.com/@a/2"]))

    assert get_audio_allowance(session, usuario.id).used == 1


# ============================================================================ nunca consome generation/batch de vídeo
def test_lote_de_audio_nunca_toca_generations_ou_batches(auth_client, factory, session, tmp_path):
    usuario = factory.user()
    login_directly(auth_client, session, usuario)
    give(factory, session, usuario, "weekly", now=resolve_now())
    pares_arquivo = _arquivos(tmp_path, 1)

    with patch("services.audio_flow.download_audio_source", return_value=fake_audio_download_result(pares_arquivo[0][0])), \
         patch("services.audio_flow.process_audio", return_value=fake_audio_processing_result(pares_arquivo[0][1])):
        auth_client.post("/api/audio/batch", json=_body("r1", ["https://www.tiktok.com/@a/1"]))

    assert session.query(Generation).count() == 0
    assert session.query(Batch).count() == 0


# ============================================================================ idempotência / conflito
def test_mesmo_request_id_repetido_nao_consome_nova_vaga(auth_client, factory, session, tmp_path):
    usuario = factory.user()
    login_directly(auth_client, session, usuario)
    give(factory, session, usuario, "weekly", now=resolve_now())
    pares_arquivo = _arquivos(tmp_path, 1)

    with patch("services.audio_flow.download_audio_source", return_value=fake_audio_download_result(pares_arquivo[0][0])), \
         patch("services.audio_flow.process_audio", return_value=fake_audio_processing_result(pares_arquivo[0][1])):
        r1 = auth_client.post("/api/audio/batch", json=_body("repetido", ["https://www.tiktok.com/@a/1"]))
        r2 = auth_client.post("/api/audio/batch", json=_body("repetido", ["https://www.tiktok.com/@a/1"]))

    batch_id_1 = next(d["batch_id"] for t, d in _eventos(r1) if t == "batch_reserved")
    batch_id_2 = next(d["batch_id"] for t, d in _eventos(r2) if t == "batch_reserved")
    assert batch_id_1 == batch_id_2
    assert get_audio_allowance(session, usuario.id).used == 1


def test_mesmo_request_id_com_tamanho_diferente_recebe_conflict(auth_client, factory, session, tmp_path):
    usuario = factory.user()
    login_directly(auth_client, session, usuario)
    give(factory, session, usuario, "weekly", now=resolve_now())
    pares_arquivo = _arquivos(tmp_path, 1)

    with patch("services.audio_flow.download_audio_source", return_value=fake_audio_download_result(pares_arquivo[0][0])), \
         patch("services.audio_flow.process_audio", return_value=fake_audio_processing_result(pares_arquivo[0][1])):
        auth_client.post("/api/audio/batch", json=_body("conflito", ["https://www.tiktok.com/@a/1"]))
        r2 = auth_client.post("/api/audio/batch", json=_body("conflito", ["https://www.tiktok.com/@a/1", "https://www.tiktok.com/@a/2"]))

    eventos = _eventos(r2)
    assert eventos[-1][0] == "error"
    assert eventos[-1][1]["code"] == "audio_batch_request_conflict"


# ============================================================================ filename inválido
def test_filename_com_path_traversal_recebe_evento_error(auth_client, factory, session):
    usuario = factory.user()
    login_directly(auth_client, session, usuario)
    give(factory, session, usuario, "weekly", now=resolve_now())
    resposta = auth_client.post(
        "/api/audio/batch",
        json=_body("r1", ["https://www.tiktok.com/@a/1"], filenames=["../etc/passwd"]),
    )
    eventos = _eventos(resposta)
    assert [t for t, _ in eventos] == ["error"]
    assert eventos[0][1]["code"] == "invalid_filename"


def test_filename_invalido_nao_consome_nenhuma_cota(auth_client, factory, session):
    usuario = factory.user()
    login_directly(auth_client, session, usuario)
    give(factory, session, usuario, "weekly", now=resolve_now())
    auth_client.post("/api/audio/batch", json=_body("r1", ["https://www.tiktok.com/@a/1"], filenames=["/etc/passwd"]))
    assert get_audio_allowance(session, usuario.id).used == 0
    assert session.query(AudioBatch).count() == 0


# ============================================================================ download individual do item (reaproveita a rota avulsa)
def test_item_do_lote_pode_ser_baixado_pela_rota_avulsa_de_sempre(auth_client, factory, session, tmp_path):
    usuario = factory.user()
    login_directly(auth_client, session, usuario)
    give(factory, session, usuario, "weekly", now=resolve_now())
    pares_arquivo = _arquivos(tmp_path, 1)

    with patch("services.audio_flow.download_audio_source", return_value=fake_audio_download_result(pares_arquivo[0][0])), \
         patch("services.audio_flow.process_audio", return_value=fake_audio_processing_result(pares_arquivo[0][1])):
        resposta = auth_client.post("/api/audio/batch", json=_body("r1", ["https://www.tiktok.com/@a/1"]))
    item = next(d for t, d in _eventos(resposta) if t == "item_complete")

    download = auth_client.get(f"/api/audio/{item['extraction_id']}/download")
    assert download.status_code == 200
    assert download.headers["content-type"] == "audio/mpeg"


# ============================================================================ GET /api/audio/batches/{id}/download (ZIP)
def test_zip_sem_sessao_401(auth_client):
    resposta = auth_client.get("/api/audio/batches/1/download")
    assert resposta.status_code == 401


def test_zip_de_lote_inexistente_404(auth_client, factory, session):
    login_directly(auth_client, session, factory.user())
    resposta = auth_client.get("/api/audio/batches/999999/download")
    assert resposta.status_code == 404


def test_zip_de_lote_de_outro_usuario_404(auth_client, factory, session):
    dono = factory.user()
    atacante = factory.user()
    lote = AudioBatch(user_id=dono.id, request_id="x", item_count=1, created_at=utcnow())
    session.add(lote)
    session.commit()
    login_directly(auth_client, session, atacante)
    resposta = auth_client.get(f"/api/audio/batches/{lote.id}/download")
    assert resposta.status_code == 404


def test_zip_sem_nenhum_item_concluido_404(auth_client, factory, session):
    from datetime import date

    usuario = factory.user()
    login_directly(auth_client, session, usuario)
    lote = AudioBatch(user_id=usuario.id, request_id="x", item_count=1, created_at=utcnow())
    session.add(lote)
    session.flush()
    session.add(AudioExtraction(
        user_id=usuario.id, batch_id=lote.id, position=1, status="failed",
        period_day=date(2026, 9, 21), error_code="x", created_at=utcnow(),
    ))
    session.commit()
    resposta = auth_client.get(f"/api/audio/batches/{lote.id}/download")
    assert resposta.status_code == 404


def test_zip_com_sucesso_contem_so_os_itens_concluidos_e_se_chama_klango_audios(auth_client, factory, session, tmp_path):
    from datetime import date

    usuario = factory.user()
    login_directly(auth_client, session, usuario)
    lote = AudioBatch(user_id=usuario.id, request_id="x", item_count=2, created_at=utcnow())
    session.add(lote)
    session.flush()
    chave_ok = result_storage.save(_criar_arquivo(tmp_path, "ok.mp3", b"conteudo ok"), size_bytes=11, extension="mp3")
    session.add(AudioExtraction(
        user_id=usuario.id, batch_id=lote.id, position=1, status="completed",
        period_day=date(2026, 9, 21), output_storage_key=chave_ok,
        output_expires_at=utcnow() + timedelta(minutes=10), created_at=utcnow(),
    ))
    session.add(AudioExtraction(
        user_id=usuario.id, batch_id=lote.id, position=2, status="failed",
        period_day=date(2026, 9, 21), error_code="x", created_at=utcnow(),
    ))
    session.commit()

    resposta = auth_client.get(f"/api/audio/batches/{lote.id}/download")
    assert resposta.status_code == 200
    assert resposta.headers["content-type"] == "application/zip"
    assert resposta.headers["content-disposition"] == 'attachment; filename="klango_audios.zip"'
    with zipfile.ZipFile(BytesIO(resposta.content)) as zf:
        nomes = zf.namelist()
        assert len(nomes) == 1  # só o item concluído -- o failed nunca entra no ZIP
        with zf.open(nomes[0]) as arquivo:
            assert arquivo.read() == b"conteudo ok"
