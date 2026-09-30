"""Free anônimo (aprovação do CÉREBRO): identidade por cookie, cota, ownership e promoção
anônimo -> autenticado. Não redecide nada que services/usage.py já testa (cota/rollback/
idempotência genéricos) -- só o que é NOVO nesta etapa: como o `user_id` é resolvido sem sessão,
como o cookie chega na resposta (inclusive em StreamingResponse), e a transição para login.
"""
from fastapi.testclient import TestClient
from sqlalchemy import select

from database.models import AnonymousIdentity, User
from helpers_auth import BASE_URL
from main import app
from utils.security import hash_anon_device_token
from utils.time_sp import now_sp, sp_day, sp_week_start


def _anon_user_id(session, cookie_value: str) -> int:
    return session.execute(
        select(AnonymousIdentity.user_id).where(
            AnonymousIdentity.device_token_hash == hash_anon_device_token(cookie_value)
        )
    ).scalar_one()


def _novo_client() -> TestClient:
    """Um segundo cliente, com seu PRÓPRIO cookie jar, contra o MESMO app/banco de teste
    (os dependency_overrides de auth_client já valem para o app inteiro) -- simula outro
    dispositivo/navegador."""
    client = TestClient(app, base_url=BASE_URL)
    client.headers.update({"Origin": BASE_URL})
    return client


# ============================================================================ criação e reaproveitamento
def test_primeira_visita_cria_identidade_com_cookie_correto(auth_client):
    resposta = auth_client.get("/api/me/status")
    assert resposta.status_code == 200

    cookie = resposta.headers["set-cookie"]
    baixo = cookie.lower()
    assert cookie.startswith("minhoca_anon=")
    assert "httponly" in baixo and "samesite=lax" in baixo and "path=/" in baixo
    assert "secure" not in baixo  # desenvolvimento em http
    assert "max-age=" in baixo
    token = cookie.split(";")[0].split("=", 1)[1]
    assert len(token) >= 32 and token not in resposta.text  # nunca no corpo


def test_segunda_requisicao_reaproveita_o_mesmo_cookie_sem_recriar(auth_client):
    primeira = auth_client.get("/api/me/status")
    token = primeira.headers["set-cookie"].split(";")[0].split("=", 1)[1]

    segunda = auth_client.get("/api/me/status")  # o TestClient reenvia o cookie sozinho
    assert segunda.status_code == 200
    assert "set-cookie" not in segunda.headers  # identidade já resolvida -- nada novo para setar
    assert auth_client.cookies.get("minhoca_anon") == token  # nenhum cookie novo substituiu o antigo


def test_mesma_identidade_em_multiplas_requisicoes_e_a_mesma_conta_para_cota(auth_client, session):
    primeira = auth_client.get("/api/me/status")
    token = primeira.headers["set-cookie"].split(";")[0].split("=", 1)[1]
    anon_user_id = _anon_user_id(session, token)

    usuario = session.get(User, anon_user_id)
    session.add_all(
        [
            _completed_free_generation(usuario, n)
            for n in range(3)
        ]
    )
    session.commit()

    status = auth_client.get("/api/me/status")
    assert status.status_code == 200
    assert status.json()["usage"] == {"used": 3, "limit": 5, "remaining": 2, "period": "week"}


def _completed_free_generation(usuario, indice: int):
    from database.models import Generation

    return Generation(
        user_id=usuario.id,
        request_id=f"anon-hist-{usuario.id}-{indice}",
        plan_code="free",
        status="completed",
        period_day=sp_day(now_sp()),
        period_week=sp_week_start(now_sp()),
    )


# ============================================================================ sessão autenticada vence
def test_sessao_autenticada_vence_identidade_anonima(auth_client, factory, session):
    from helpers_generation_flow import login_directly

    anon_resposta = auth_client.get("/api/me/status")
    anon_token = anon_resposta.headers["set-cookie"].split(";")[0].split("=", 1)[1]
    anon_user_id = _anon_user_id(session, anon_token)
    anon_usuario = session.get(User, anon_user_id)
    session.add(_completed_free_generation(anon_usuario, 0))
    session.commit()

    autenticado = factory.user()
    _entitlement_semanal(factory, autenticado)
    login_directly(auth_client, session, autenticado)  # planta o cookie de SESSÃO no MESMO client

    status = auth_client.get("/api/me/status")  # agora tem os DOIS cookies (sessão + anônimo)
    assert status.status_code == 200
    assert status.json()["plan"]["code"] == "weekly"  # do usuário AUTENTICADO, não do anônimo (Free)
    assert "set-cookie" not in status.headers or "minhoca_anon" not in status.headers.get("set-cookie", "")


def _entitlement_semanal(factory, usuario):
    from utils.time_sp import now_sp

    now = now_sp()
    payment = factory.payment(usuario, plan_code="weekly", status="approved")
    return factory.entitlement(
        payment, plan_code="weekly", duration_days=7, starts_at=now,
        expires_at=now.replace(year=now.year + 1),
    )


# ============================================================================ cota Free (5/semana) e 6ª tentativa
def test_quota_free_5_por_semana_e_6a_tentativa_bloqueada(auth_client, session):
    primeira = auth_client.get("/api/me/status")
    token = primeira.headers["set-cookie"].split(";")[0].split("=", 1)[1]
    anon_user_id = _anon_user_id(session, token)
    usuario = session.get(User, anon_user_id)
    session.add_all([_completed_free_generation(usuario, n) for n in range(5)])
    session.commit()

    status = auth_client.get("/api/me/status")
    assert status.json()["usage"] == {"used": 5, "limit": 5, "remaining": 0, "period": "week"}

    sexta = auth_client.post("/api/generations", json={"url": "https://www.tiktok.com/@a/video/1"})
    assert sexta.status_code == 200  # o stream sempre abre -- o erro vem como evento SSE
    eventos = _eventos(sexta)
    assert eventos[-1] == (
        "error",
        {"detail": "Você atingiu o limite de gerações do seu plano.", "code": "quota_exceeded"},
    )


def _eventos(resposta) -> list[tuple[str, dict]]:
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


# ============================================================================ ownership do download
def test_download_individual_com_identidade_anonima_funciona(auth_client, session, tmp_path, monkeypatch):
    from datetime import timedelta

    from database.models import Generation
    from database.types import utcnow
    from services import result_storage

    monkeypatch.setattr("services.result_storage.RESULT_STORAGE_DIR", str(tmp_path / "results"))
    origem = tmp_path / "saida.mp4"
    origem.write_bytes(b"conteudo do mp4")
    chave = result_storage.save(origem, size_bytes=origem.stat().st_size)

    resposta = auth_client.get("/api/me/status")  # cria a identidade anônima
    token = resposta.headers["set-cookie"].split(";")[0].split("=", 1)[1]
    anon_user_id = _anon_user_id(session, token)
    usuario = session.get(User, anon_user_id)
    geracao = Generation(
        user_id=usuario.id, request_id="anon-download-1", plan_code="free", status="completed",
        period_day=sp_day(now_sp()), period_week=sp_week_start(now_sp()),
        output_storage_key=chave, output_expires_at=utcnow() + timedelta(minutes=10),
    )
    session.add(geracao)
    session.commit()

    download = auth_client.get(f"/api/generations/{geracao.id}/download")
    assert download.status_code == 200
    assert download.content == b"conteudo do mp4"


def test_geracao_de_outro_dispositivo_e_recusada(auth_client, session, tmp_path, monkeypatch):
    from datetime import timedelta

    from database.models import Generation
    from database.types import utcnow
    from services import result_storage

    monkeypatch.setattr("services.result_storage.RESULT_STORAGE_DIR", str(tmp_path / "results"))
    origem = tmp_path / "saida.mp4"
    origem.write_bytes(b"conteudo do dono")
    chave = result_storage.save(origem, size_bytes=origem.stat().st_size)

    dono_resposta = auth_client.get("/api/me/status")
    dono_token = dono_resposta.headers["set-cookie"].split(";")[0].split("=", 1)[1]
    dono_user_id = _anon_user_id(session, dono_token)
    dono = session.get(User, dono_user_id)
    geracao_do_dono = Generation(
        user_id=dono.id, request_id="anon-download-dono", plan_code="free", status="completed",
        period_day=sp_day(now_sp()), period_week=sp_week_start(now_sp()),
        output_storage_key=chave, output_expires_at=utcnow() + timedelta(minutes=10),
    )
    session.add(geracao_do_dono)
    session.commit()

    outro_dispositivo = _novo_client()
    resposta = outro_dispositivo.get(f"/api/generations/{geracao_do_dono.id}/download")
    assert resposta.status_code == 404  # mesmo 404 genérico de sempre -- nunca revela que existe


# ============================================================================ promoção anônimo -> autenticado
def test_promocao_anonimo_para_email_novo_preserva_o_mesmo_user_id_e_o_historico(auth_client, fake_sender, session):
    """auth_client já sobrescreve get_email_sender com a fixture `fake_sender` (ver conftest.py)
    -- basta usá-la para ler o código, mesmo padrão de tests/test_auth_routes.py."""
    anon_resposta = auth_client.get("/api/me/status")
    anon_token = anon_resposta.headers["set-cookie"].split(";")[0].split("=", 1)[1]
    anon_user_id = _anon_user_id(session, anon_token)
    anon_usuario = session.get(User, anon_user_id)
    session.add_all([_completed_free_generation(anon_usuario, n) for n in range(2)])
    session.commit()

    email = "visitante-promovido@example.com"
    assert auth_client.post("/api/auth/request-code", json={"email": email}).status_code == 200
    codigo = fake_sender.last_code(email)
    verificacao = auth_client.post("/api/auth/verify-code", json={"email": email, "code": codigo})
    assert verificacao.status_code == 200
    assert verificacao.json() == {"email": email}

    promovido = session.get(User, anon_user_id)
    session.refresh(promovido)
    assert promovido.email == email  # MESMO id, e-mail real -- não foi criado um id novo
    assert promovido.email_verified_at is not None

    status_logado = auth_client.get("/api/me/status")  # agora com sessão de verdade
    assert status_logado.json()["usage"]["used"] == 2  # histórico do anônimo preservado


def test_login_com_email_ja_existente_nao_funde_e_nao_corrompe_ownership(auth_client, fake_sender, factory, session):
    dono_existente = factory.user(email="ja-tenho-conta@example.com")
    dono_existente_id = dono_existente.id

    anon_resposta = auth_client.get("/api/me/status")
    anon_token = anon_resposta.headers["set-cookie"].split(";")[0].split("=", 1)[1]
    anon_user_id = _anon_user_id(session, anon_token)
    anon_usuario = session.get(User, anon_user_id)
    session.add(_completed_free_generation(anon_usuario, 0))
    session.commit()
    assert anon_user_id != dono_existente_id

    email = "ja-tenho-conta@example.com"
    assert auth_client.post("/api/auth/request-code", json={"email": email}).status_code == 200
    codigo = fake_sender.last_code(email)
    verificacao = auth_client.post("/api/auth/verify-code", json={"email": email, "code": codigo})
    assert verificacao.status_code == 200
    assert verificacao.json() == {"email": email}

    # login entrou na conta JÁ EXISTENTE -- sem merge, sem alterar o id de ninguém
    anon_intacto = session.get(User, anon_user_id)
    session.refresh(anon_intacto)
    assert anon_intacto.id == anon_user_id
    assert anon_intacto.email.endswith("@device.invalid")  # continua órfão, intacto

    existente_intacto = session.get(User, dono_existente_id)
    session.refresh(existente_intacto)
    assert existente_intacto.email == email


# ============================================================================ regressão: pagamentos
def test_pagamentos_continuam_exigindo_login_mesmo_com_identidade_anonima(auth_client):
    auth_client.get("/api/me/status")  # estabelece uma identidade anônima
    resposta = auth_client.post("/api/payments", json={"plan_code": "weekly", "method": "pix"})
    assert resposta.status_code == 401
    assert resposta.json()["code"] == "not_authenticated"
