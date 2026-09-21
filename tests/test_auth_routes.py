import logging
import re

from fastapi.testclient import TestClient

from database.session import get_session
from main import app
from routes.deps import get_email_sender

EMAIL = "ana@example.com"


def _pedir(client, email=EMAIL):
    return client.post("/api/auth/request-code", json={"email": email})


def _logar(client, sender, email=EMAIL):
    assert _pedir(client, email).status_code == 200
    return client.post("/api/auth/verify-code", json={"email": email, "code": sender.last_code(email)})


# ============================================================================ request-code
def test_request_code_200_com_corpo_e_no_store(auth_client, fake_sender):
    resposta = _pedir(auth_client)
    assert resposta.status_code == 200
    assert resposta.json() == {"status": "sent", "expires_in": 600, "resend_after": 60}
    assert resposta.headers["cache-control"] == "no-store"
    assert len(fake_sender.messages) == 1


def test_request_code_email_invalido_400(auth_client):
    resposta = _pedir(auth_client, "isto-nao-e-email")
    assert resposta.status_code == 400
    assert resposta.json() == {"detail": "Informe um e-mail válido.", "code": "invalid_email"}
    assert resposta.headers["cache-control"] == "no-store"


def test_corpo_invalido_segue_o_formato_de_erro_da_auth(auth_client):
    for corpo in ({}, {"email": 123}, {"email": None}, {"email": "a" * 400}):
        resposta = auth_client.post("/api/auth/request-code", json=corpo)
        assert resposta.status_code == 400, corpo
        assert resposta.json() == {"detail": "Requisição inválida.", "code": "invalid_request"}
        assert resposta.headers["cache-control"] == "no-store"
    assert auth_client.post("/api/auth/verify-code", json={"email": EMAIL}).json()["code"] == "invalid_request"


def test_request_code_429_com_retry_after(auth_client, fake_sender):
    assert _pedir(auth_client).status_code == 200
    resposta = _pedir(auth_client)
    assert resposta.status_code == 429
    assert resposta.json()["code"] == "rate_limited"
    assert 1 <= int(resposta.headers["retry-after"]) <= 60
    assert resposta.headers["cache-control"] == "no-store"
    assert len(fake_sender.messages) == 1


def test_request_code_503_generico_quando_o_sender_falha(auth_client, fake_sender):
    fake_sender.fail_with = RuntimeError("erro interno com segredo=xyz")
    resposta = _pedir(auth_client)
    assert resposta.status_code == 503
    assert resposta.json() == {
        "detail": "Não foi possível enviar o código agora. Tente novamente em instantes.",
        "code": "email_unavailable",
    }
    assert "xyz" not in resposta.text and resposta.headers["cache-control"] == "no-store"


def test_enumeracao_respostas_iguais_para_email_existente_e_novo(auth_client, factory):
    factory.user(email="existe@example.com")
    a = _pedir(auth_client, "existe@example.com")
    b = _pedir(auth_client, "novo@example.com")
    assert (a.status_code, a.json()) == (b.status_code, b.json())
    assert dict(a.headers).get("content-length") == dict(b.headers).get("content-length")


# ============================================================================ verify-code / cookie / me / logout
def test_verify_code_abre_a_sessao_com_cookie_seguro(auth_client, fake_sender):
    resposta = _logar(auth_client, fake_sender)
    assert resposta.status_code == 200
    assert resposta.json() == {"email": EMAIL}  # o token NUNCA vai no corpo
    assert resposta.headers["cache-control"] == "no-store"

    cookie = resposta.headers["set-cookie"]
    baixo = cookie.lower()
    assert cookie.startswith("minhoca_session=")
    assert "httponly" in baixo and "samesite=lax" in baixo and "path=/" in baixo
    assert "max-age=2592000" in baixo  # 30 dias
    assert "secure" not in baixo  # desenvolvimento em http
    assert "domain" not in baixo
    token = cookie.split(";")[0].split("=", 1)[1]
    assert len(token) == 43 and token not in resposta.text


def test_verify_code_invalido_400_com_mensagem_unica(auth_client, fake_sender):
    _pedir(auth_client)
    certo = fake_sender.last_code(EMAIL)
    errado = "000000" if certo != "000000" else "111111"
    a = auth_client.post("/api/auth/verify-code", json={"email": EMAIL, "code": errado})
    b = auth_client.post("/api/auth/verify-code", json={"email": "ninguem@example.com", "code": "123456"})
    c = auth_client.post("/api/auth/verify-code", json={"email": EMAIL, "code": "abc"})
    for r in (a, b, c):
        assert r.status_code == 400
        assert r.json() == {"detail": "Código inválido ou expirado. Solicite um novo código.", "code": "invalid_code"}
        assert r.headers["cache-control"] == "no-store" and "set-cookie" not in r.headers


def test_me_sem_sessao_401(auth_client):
    resposta = auth_client.get("/api/auth/me")
    assert resposta.status_code == 401
    assert resposta.json() == {"detail": "Sessão inválida ou expirada.", "code": "not_authenticated"}
    assert resposta.headers["cache-control"] == "no-store"


def test_fluxo_completo_login_me_logout(auth_client, fake_sender):
    assert _logar(auth_client, fake_sender).status_code == 200

    me = auth_client.get("/api/auth/me")  # o cookie da sessão volta sozinho
    assert me.status_code == 200
    assert me.json() == {"email": EMAIL, "email_verified": True}
    assert me.headers["cache-control"] == "no-store"

    saida = auth_client.post("/api/auth/logout")
    assert saida.status_code == 200 and saida.json() == {"status": "ok"}
    assert "max-age=0" in saida.headers["set-cookie"].lower()  # cookie apagado

    assert auth_client.get("/api/auth/me").status_code == 401  # a sessão foi revogada no servidor


def test_logout_repetido_e_sem_sessao_nao_da_erro(auth_client, fake_sender):
    assert auth_client.post("/api/auth/logout").status_code == 200  # sem sessão
    _logar(auth_client, fake_sender)
    token = auth_client.cookies.get("minhoca_session")
    assert token
    assert auth_client.post("/api/auth/logout").status_code == 200

    com_token_antigo = {"Cookie": f"minhoca_session={token}"}  # quem guardou o token e insiste em usá-lo
    assert auth_client.post("/api/auth/logout", headers=com_token_antigo).status_code == 200  # repetir é seguro
    assert auth_client.get("/api/auth/me", headers=com_token_antigo).status_code == 401  # revogado NO SERVIDOR


def test_token_forjado_e_rejeitado(auth_client):
    forjado = {"Cookie": "minhoca_session=" + "x" * 43}
    resposta = auth_client.get("/api/auth/me", headers=forjado)
    assert resposta.status_code == 401 and resposta.json()["code"] == "not_authenticated"


def test_a_sessao_de_um_cliente_nao_vale_para_outro(auth_client, fake_sender, maker):
    _logar(auth_client, fake_sender)
    outro = TestClient(app, base_url="http://localhost:8000")
    assert outro.get("/api/auth/me").status_code == 401


# ============================================================================ produção: cookie __Host- e Secure
def _producao(use_settings):
    return use_settings(
        env="production",
        public_base_url="https://minhoca.example",
        auth_secret_key="a" * 40,
        ip_hash_secret="b" * 40,
        email_sender="",
    )


def test_cookie_em_producao_tem_prefixo_host_e_secure(maker, fake_sender, use_settings):
    _producao(use_settings)

    def _session():
        s = maker()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_email_sender] = lambda: fake_sender
    try:
        cliente = TestClient(app, base_url="https://minhoca.example")
        cliente.headers.update({"Origin": "https://minhoca.example"})
        resposta = _logar(cliente, fake_sender)
        assert resposta.status_code == 200
        cookie = resposta.headers["set-cookie"]
        baixo = cookie.lower()
        assert cookie.startswith("__Host-minhoca_session=")
        assert all(flag in baixo for flag in ("httponly", "secure", "samesite=lax", "path=/"))
        assert "domain" not in baixo  # exigência do prefixo __Host-
        assert cliente.get("/api/auth/me").status_code == 200

        saida = cliente.post("/api/auth/logout")
        apagar = saida.headers["set-cookie"].lower()
        assert saida.headers["set-cookie"].startswith("__Host-minhoca_session=")
        assert "secure" in apagar and "max-age=0" in apagar
    finally:
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_email_sender, None)


def test_origem_de_producao_e_a_public_base_url(auth_client, use_settings):
    _producao(use_settings)
    # o auth_client manda Origin http://localhost:8000, que deixa de ser a origem permitida
    assert _pedir(auth_client).status_code == 403


def test_console_sender_e_recusado_em_producao(maker, use_settings, caplog):
    use_settings(
        env="production",
        public_base_url="https://minhoca.example",
        auth_secret_key="a" * 40,
        ip_hash_secret="b" * 40,
        email_sender="console",
    )

    def _session():
        s = maker()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_session] = _session  # sem override do sender: usa o sender real do ambiente
    try:
        cliente = TestClient(app, base_url="https://minhoca.example")
        cliente.headers.update({"Origin": "https://minhoca.example"})
        with caplog.at_level(logging.INFO, logger="minhoca"):
            resposta = cliente.post("/api/auth/request-code", json={"email": EMAIL})
    finally:
        app.dependency_overrides.pop(get_session, None)
    assert resposta.status_code == 503 and resposta.json()["code"] == "email_unavailable"
    assert "[EMAIL:console]" not in caplog.text  # nada de código no log em produção


def test_sem_sender_configurado_em_producao_responde_503(maker, use_settings):
    use_settings(
        env="production",
        public_base_url="https://minhoca.example",
        auth_secret_key="a" * 40,
        ip_hash_secret="b" * 40,
        email_sender="",
    )

    def _session():
        s = maker()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_session] = _session
    try:
        cliente = TestClient(app, base_url="https://minhoca.example")
        cliente.headers.update({"Origin": "https://minhoca.example"})
        resposta = cliente.post("/api/auth/request-code", json={"email": EMAIL})
    finally:
        app.dependency_overrides.pop(get_session, None)
    assert resposta.status_code == 503


# ============================================================================ desenvolvimento com o ConsoleEmailSender real
def test_em_desenvolvimento_o_codigo_aparece_no_console(maker, caplog):
    def _session():
        s = maker()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_session] = _session  # sender real: ConsoleEmailSender (EMAIL_SENDER=console)
    try:
        cliente = TestClient(app, base_url="http://localhost:8000")
        cliente.headers.update({"Origin": "http://localhost:8000"})
        with caplog.at_level(logging.INFO, logger="minhoca"):
            resposta = cliente.post("/api/auth/request-code", json={"email": EMAIL})
        assert resposta.status_code == 200
        codigo = re.search(r"\b(\d{6})\b", caplog.text.split("[EMAIL:console]")[1]).group(1)
        entrada = cliente.post("/api/auth/verify-code", json={"email": EMAIL, "code": codigo})
        assert entrada.status_code == 200
    finally:
        app.dependency_overrides.pop(get_session, None)
