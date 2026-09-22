"""POST /api/generations: contrato HTTP, autenticação, ownership e tradução de erros."""
from pathlib import Path
from unittest.mock import patch

from database.models import Generation
from download.errors import DownloadFailedError, InvalidUrlError, UnsupportedPlatformError
from download.platform import Platform
from helpers_generation_flow import fake_download_result, fake_processing_result, login_directly
from processor.errors import FfmpegFailedError
from services.usage import QuotaExceededError, get_allowance, reserve_generation


def _files(tmp_path):
    download_path = tmp_path / "baixado.mp4"
    download_path.write_bytes(b"x")
    output_path = tmp_path / "saida.mp4"
    output_path.write_bytes(b"y")
    return download_path, output_path


# ============================================================================ autenticação
def test_sem_sessao_401(auth_client):
    resposta = auth_client.post("/api/generations", json={"url": "https://www.tiktok.com/@a/video/1"})
    assert resposta.status_code == 401
    assert resposta.json()["code"] == "not_authenticated"


def test_corpo_sem_url_e_recusado(auth_client, factory, session):
    login_directly(auth_client, session, factory.user())
    resposta = auth_client.post("/api/generations", json={})
    assert resposta.status_code in (400, 422)  # validação do Pydantic/handler de requisição inválida


# ============================================================================ sucesso
def test_sucesso_devolve_o_contrato_esperado(auth_client, factory, session, tmp_path):
    usuario = factory.user()
    login_directly(auth_client, session, usuario)
    download_path, output_path = _files(tmp_path)

    with patch("services.generation_flow.download_video", return_value=fake_download_result(download_path, platform=Platform.TIKTOK)), \
         patch("services.generation_flow.process_video", return_value=fake_processing_result(output_path, sha="c" * 64, duration=9.1, size=321)):
        resposta = auth_client.post("/api/generations", json={"url": "https://www.tiktok.com/@a/video/1"})

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert set(corpo) == {"generation_id", "status", "platform", "size_bytes", "duration_seconds", "output_sha256"}
    assert corpo["status"] == "completed"
    assert corpo["platform"] == "tiktok"
    assert corpo["output_sha256"] == "c" * 64
    assert corpo["size_bytes"] == 321
    assert resposta.headers["cache-control"] == "no-store"


# ============================================================================ ownership
def test_identidade_vem_da_sessao_nao_do_corpo(auth_client, factory, session, tmp_path):
    dono = factory.user()
    outro = factory.user()
    login_directly(auth_client, session, dono)
    download_path, output_path = _files(tmp_path)

    with patch("services.generation_flow.download_video", return_value=fake_download_result(download_path)), \
         patch("services.generation_flow.process_video", return_value=fake_processing_result(output_path)):
        resposta = auth_client.post(
            "/api/generations",
            json={"url": "https://www.tiktok.com/@a/video/1", "user_id": outro.id, "email": outro.email, "account_id": outro.id},
        )

    assert resposta.status_code == 200
    session.expire_all()
    linha = session.get(Generation, resposta.json()["generation_id"])
    assert linha.user_id == dono.id  # os campos extras no corpo são ignorados pelo Pydantic


def test_um_usuario_nao_consome_cota_de_outro(auth_client, factory, session, tmp_path):
    usuario_a = factory.user()
    usuario_b = factory.user()
    login_directly(auth_client, session, usuario_a)
    download_path, output_path = _files(tmp_path)

    with patch("services.generation_flow.download_video", return_value=fake_download_result(download_path)), \
         patch("services.generation_flow.process_video", return_value=fake_processing_result(output_path)):
        resposta = auth_client.post("/api/generations", json={"url": "https://www.tiktok.com/@a/video/1"})

    assert resposta.status_code == 200
    assert get_allowance(session, usuario_a.id).used == 1
    assert get_allowance(session, usuario_b.id).used == 0  # intacto


# ============================================================================ tradução de erros
def test_url_invalida_422(auth_client, factory, session):
    login_directly(auth_client, session, factory.user())
    resposta = auth_client.post("/api/generations", json={"url": "ftp://x.com"})
    assert resposta.status_code == 422
    corpo = resposta.json()
    assert corpo["code"] == "invalid_url"
    assert corpo["detail"] == InvalidUrlError().user_message


def test_plataforma_nao_suportada_422(auth_client, factory, session):
    login_directly(auth_client, session, factory.user())
    resposta = auth_client.post("/api/generations", json={"url": "https://example.com/video"})
    assert resposta.status_code == 422
    assert resposta.json()["code"] == "unsupported_platform"


def test_cota_esgotada_429(auth_client, factory, session):
    usuario = factory.user()
    login_directly(auth_client, session, usuario)
    for i in range(5):
        reserve_generation(session, user_id=usuario.id, request_id=f"pre-{i}")

    resposta = auth_client.post("/api/generations", json={"url": "https://www.tiktok.com/@a/video/1"})
    assert resposta.status_code == 429
    assert resposta.json()["code"] == "quota_exceeded"


def test_download_falha_422_e_nao_vaza_detalhe_tecnico(auth_client, factory, session):
    login_directly(auth_client, session, factory.user())
    with patch("services.generation_flow.download_video", side_effect=DownloadFailedError("stderr sensível: /caminho/interno")):
        resposta = auth_client.post("/api/generations", json={"url": "https://www.tiktok.com/@a/video/1"})
    assert resposta.status_code == 422
    assert resposta.json()["code"] == "download_failed"
    assert "/caminho/interno" not in resposta.text


def test_processamento_falha_422(auth_client, factory, session, tmp_path):
    login_directly(auth_client, session, factory.user())
    download_path, _ = _files(tmp_path)
    with patch("services.generation_flow.download_video", return_value=fake_download_result(download_path)), \
         patch("services.generation_flow.process_video", side_effect=FfmpegFailedError("falha")):
        resposta = auth_client.post("/api/generations", json={"url": "https://www.tiktok.com/@a/video/1"})
    assert resposta.status_code == 422
    assert resposta.json()["code"] == "ffmpeg_failed"


def test_resposta_de_erro_segue_o_formato_padrao(auth_client, factory, session):
    login_directly(auth_client, session, factory.user())
    resposta = auth_client.post("/api/generations", json={"url": "ftp://x.com"})
    assert set(resposta.json()) == {"detail", "code"}
    assert resposta.headers["cache-control"] == "no-store"
