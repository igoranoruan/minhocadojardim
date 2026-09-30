"""POST /api/generations: contrato HTTP, autenticação, ownership e tradução de erros.

Desde a barra de progresso real (SSE), a rota devolve `text/event-stream`: 200 sempre que o
stream chega a abrir, com os eventos (`status`/`progress`/`complete`/`error`) carregando o que
antes eram status HTTP diferentes (422/429/503) ou o corpo de sucesso direto.

Free anônimo (aprovação do CÉREBRO): a rota usa get_generation_user, não get_current_user --
sessão ausente já não é mais 401 (o visitante ganha uma identidade anônima automaticamente; ver
tests/test_anon_identity.py para a suíte completa desse comportamento). Só um erro real de
domínio (download/processamento/cota/etc.) vira o evento `error` dentro do stream 200.

O TestClient roda o ASGI da ponta a ponta e só devolve o Response depois do generator terminar
(StreamingResponse é drenado internamente) -- por isso `resposta.text` já contém o stream inteiro,
pronto para ser parseado por `_eventos()`, sem precisar de leitura incremental real nos testes.
"""
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


def _eventos(resposta) -> list[tuple[str, dict]]:
    """Parseia o corpo `text/event-stream` inteiro (já drenado pelo TestClient) em
    [(tipo, dados), ...], na ordem em que chegaram."""
    import json

    eventos = []
    for bloco in resposta.text.strip("\n").split("\n\n"):
        if not bloco.strip():
            continue
        linhas = bloco.split("\n")
        tipo = linhas[0].removeprefix("event: ")
        dados = json.loads(linhas[1].removeprefix("data: "))
        eventos.append((tipo, dados))
    return eventos


# ============================================================================ autenticação / Free anônimo
def test_sem_sessao_cria_identidade_anonima_em_vez_de_401(auth_client):
    """Free anônimo (aprovação do CÉREBRO): sem sessão E sem cookie, a rota abre o stream mesmo
    assim (nunca 401) -- get_generation_user cria um usuário-dispositivo na primeira visita, e o
    AnonymousCookieMiddleware grava o cookie na resposta (StreamingResponse incluído -- é
    exatamente o caso que motivou o middleware em vez de Response injetado por Depends)."""
    resposta = auth_client.post("/api/generations", json={"url": "https://plataforma-invalida.example/x"})
    assert resposta.status_code == 200

    cookie = resposta.headers["set-cookie"]
    baixo = cookie.lower()
    assert cookie.startswith("minhoca_anon=")
    assert "httponly" in baixo and "samesite=lax" in baixo and "path=/" in baixo
    assert "secure" not in baixo  # desenvolvimento em http

    eventos = _eventos(resposta)
    assert eventos[-1][0] == "error"  # plataforma não suportada -- mas o stream abriu (200), nunca 401


def test_corpo_sem_url_e_recusado(auth_client, factory, session):
    """Validação do Pydantic roda antes do stream também -- continua um erro HTTP normal."""
    login_directly(auth_client, session, factory.user())
    resposta = auth_client.post("/api/generations", json={})
    assert resposta.status_code in (400, 422)


# ============================================================================ contrato do stream
def test_resposta_e_text_event_stream_com_no_store(auth_client, factory, session):
    login_directly(auth_client, session, factory.user())
    resposta = auth_client.post("/api/generations", json={"url": "ftp://x.com"})
    assert resposta.status_code == 200  # o stream ABRIU com sucesso, mesmo carregando um erro dentro
    assert resposta.headers["content-type"].startswith("text/event-stream")
    assert resposta.headers["cache-control"] == "no-store"


# ============================================================================ sucesso
def test_sucesso_sequencia_status_progress_complete(auth_client, factory, session, tmp_path):
    usuario = factory.user()
    login_directly(auth_client, session, usuario)
    download_path, output_path = _files(tmp_path)

    def process_video_com_progresso(path, *, on_progress=None):
        if on_progress:
            on_progress(0.0)
            on_progress(50.0)
            on_progress(100.0)
        return fake_processing_result(output_path, sha="c" * 64, duration=9.1, size=321)

    with patch("services.generation_flow.download_video", return_value=fake_download_result(download_path, platform=Platform.TIKTOK)), \
         patch("services.generation_flow.process_video", side_effect=process_video_com_progresso):
        resposta = auth_client.post("/api/generations", json={"url": "https://www.tiktok.com/@a/video/1"})

    assert resposta.status_code == 200
    eventos = _eventos(resposta)
    tipos = [t for t, _ in eventos]

    assert tipos[0] == "status"
    assert eventos[0][1] == {"stage": "download", "percent": None, "message": "Baixando seu vídeo..."}
    assert "progress" in tipos
    percentuais = [d["percent"] for t, d in eventos if t == "progress"]
    assert percentuais == [0.0, 50.0, 100.0]
    assert tipos[-1] == "complete"

    corpo = eventos[-1][1]
    assert set(corpo) == {"generation_id", "status", "platform", "size_bytes", "duration_seconds", "output_sha256"}
    assert corpo["status"] == "completed"
    assert corpo["platform"] == "tiktok"
    assert corpo["output_sha256"] == "c" * 64
    assert corpo["size_bytes"] == 321


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

    eventos = _eventos(resposta)
    assert eventos[-1][0] == "complete"
    session.expire_all()
    linha = session.get(Generation, eventos[-1][1]["generation_id"])
    assert linha.user_id == dono.id  # os campos extras no corpo são ignorados pelo Pydantic


def test_um_usuario_nao_consome_cota_de_outro(auth_client, factory, session, tmp_path):
    usuario_a = factory.user()
    usuario_b = factory.user()
    login_directly(auth_client, session, usuario_a)
    download_path, output_path = _files(tmp_path)

    with patch("services.generation_flow.download_video", return_value=fake_download_result(download_path)), \
         patch("services.generation_flow.process_video", return_value=fake_processing_result(output_path)):
        resposta = auth_client.post("/api/generations", json={"url": "https://www.tiktok.com/@a/video/1"})

    eventos = _eventos(resposta)  # o TestClient já drenou o stream inteiro (a thread já terminou)
    assert eventos[-1][0] == "complete"
    assert get_allowance(session, usuario_a.id).used == 1
    assert get_allowance(session, usuario_b.id).used == 0  # intacto


# ============================================================================ tradução de erros (agora dentro do evento terminal)
def test_url_invalida_vira_evento_error(auth_client, factory, session):
    login_directly(auth_client, session, factory.user())
    resposta = auth_client.post("/api/generations", json={"url": "ftp://x.com"})
    eventos = _eventos(resposta)
    assert [t for t, _ in eventos] == ["error"]
    assert eventos[0][1]["code"] == "invalid_url"
    assert eventos[0][1]["detail"] == InvalidUrlError().user_message


def test_plataforma_nao_suportada_vira_evento_error(auth_client, factory, session):
    login_directly(auth_client, session, factory.user())
    resposta = auth_client.post("/api/generations", json={"url": "https://example.com/video"})
    eventos = _eventos(resposta)
    assert eventos[-1][0] == "error"
    assert eventos[-1][1]["code"] == "unsupported_platform"


def test_cota_esgotada_vira_evento_error(auth_client, factory, session):
    usuario = factory.user()
    login_directly(auth_client, session, usuario)
    for i in range(5):
        reserve_generation(session, user_id=usuario.id, request_id=f"pre-{i}")

    resposta = auth_client.post("/api/generations", json={"url": "https://www.tiktok.com/@a/video/1"})
    eventos = _eventos(resposta)
    assert eventos[-1][0] == "error"
    assert eventos[-1][1]["code"] == "quota_exceeded"


def test_download_falha_vira_evento_error_e_nao_vaza_detalhe_tecnico(auth_client, factory, session):
    login_directly(auth_client, session, factory.user())
    with patch("services.generation_flow.download_video", side_effect=DownloadFailedError("stderr sensível: /caminho/interno")):
        resposta = auth_client.post("/api/generations", json={"url": "https://www.tiktok.com/@a/video/1"})
    eventos = _eventos(resposta)
    assert eventos[-1][0] == "error"
    assert eventos[-1][1]["code"] == "download_failed"
    assert "/caminho/interno" not in resposta.text


def test_processamento_falha_vira_evento_error(auth_client, factory, session, tmp_path):
    login_directly(auth_client, session, factory.user())
    download_path, _ = _files(tmp_path)
    with patch("services.generation_flow.download_video", return_value=fake_download_result(download_path)), \
         patch("services.generation_flow.process_video", side_effect=FfmpegFailedError("falha")):
        resposta = auth_client.post("/api/generations", json={"url": "https://www.tiktok.com/@a/video/1"})
    eventos = _eventos(resposta)
    # sequência status -> error: a fase de download ainda é emitida antes da falha de processamento
    assert [t for t, _ in eventos] == ["status", "error"]
    assert eventos[-1][1]["code"] == "ffmpeg_failed"


def test_resposta_de_erro_segue_o_formato_padrao_dentro_do_evento(auth_client, factory, session):
    login_directly(auth_client, session, factory.user())
    resposta = auth_client.post("/api/generations", json={"url": "ftp://x.com"})
    eventos = _eventos(resposta)
    assert set(eventos[-1][1]) == {"detail", "code"}
    assert resposta.headers["cache-control"] == "no-store"


def test_erro_inesperado_nao_trava_o_stream_e_nao_vaza_detalhe(auth_client, factory, session):
    """Um erro que nenhum handler antigo cobria (ex.: RuntimeError genérico) precisa, com
    streaming, virar um evento terminal explícito -- senão o cliente fica sem nenhum sinal."""
    login_directly(auth_client, session, factory.user())
    with patch("services.generation_flow.download_video", side_effect=RuntimeError("detalhe sensível=segredo123")):
        resposta = auth_client.post("/api/generations", json={"url": "https://www.tiktok.com/@a/video/1"})
    eventos = _eventos(resposta)
    assert eventos[-1][0] == "error"
    assert "segredo123" not in resposta.text
