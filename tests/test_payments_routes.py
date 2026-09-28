"""POST /api/payments, POST /api/payments/{id}/submit, GET /api/payments/public-key (Etapa 10.2):
contrato HTTP, autenticação, ownership, preço não manipulável, incompatibilidade de método, e as
duas credenciais do Mercado Pago tratadas de forma assimétrica (Access Token nunca aparece numa
resposta; Public Key é servida de propósito).

payments.client.create_payment é sempre mockado -- nenhum teste desta suíte fala com a rede real.
"""
from unittest.mock import patch

from helpers_generation_flow import login_directly


def _resposta_mp(status="approved", **overrides):
    corpo = {"id": 999888777, "status": status, "status_detail": overrides.pop("status_detail", None)}
    corpo.update(overrides)
    return {"status": 201, "response": corpo}


def _cartao_form():
    return {
        "method": "credit_card",
        "payment_method_id": "master",
        "payer": {"email": "comprador@example.com"},
        "token": "tok_do_brick_123",
        "installments": 1,
        "issuer_id": "25",
    }


def _pix_form():
    return {"method": "pix", "payment_method_id": "pix", "payer": {"email": "comprador@example.com"}}


# ============================================================================ POST /api/payments
def test_sem_sessao_401(auth_client):
    resposta = auth_client.post("/api/payments", json={"plan_code": "weekly", "method": "pix"})
    assert resposta.status_code == 401


def test_criacao_feliz_devolve_dados_publicos_sem_external_reference(auth_client, factory, session):
    login_directly(auth_client, session, factory.user())

    resposta = auth_client.post("/api/payments", json={"plan_code": "weekly", "method": "pix"})

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["plan_code"] == "weekly"
    assert corpo["method"] == "pix"
    assert corpo["status"] == "pending"
    assert corpo["amount_cents"] == 990  # WEEKLY_PLAN.price_cents
    assert "external_reference" not in corpo
    assert "external_reference" not in resposta.text


def test_preco_nao_e_manipulavel_pelo_cliente(auth_client, factory, session):
    """Enviar amount_cents/price/price_cents no corpo não muda nada -- esses campos nem existem
    no schema da rota, então nunca chegam a create_payment."""
    login_directly(auth_client, session, factory.user())

    resposta = auth_client.post(
        "/api/payments",
        json={"plan_code": "monthly", "method": "credit_card", "amount_cents": 1, "price_cents": 1, "price": 0},
    )

    assert resposta.status_code == 200
    assert resposta.json()["amount_cents"] == 1690  # MONTHLY_PLAN.price_cents, nunca 1


def test_plano_inexistente_recebe_422(auth_client, factory, session):
    login_directly(auth_client, session, factory.user())
    resposta = auth_client.post("/api/payments", json={"plan_code": "inexistente", "method": "pix"})
    assert resposta.status_code == 422


def test_plano_free_recebe_422(auth_client, factory, session):
    login_directly(auth_client, session, factory.user())
    resposta = auth_client.post("/api/payments", json={"plan_code": "free", "method": "pix"})
    assert resposta.status_code == 422


# ============================================================================ POST /api/payments/{id}/submit
def test_submit_payment_inexistente_recebe_404(auth_client, factory, session):
    login_directly(auth_client, session, factory.user())
    resposta = auth_client.post("/api/payments/999999/submit", json=_pix_form())
    assert resposta.status_code == 404


def test_submit_payment_de_outro_usuario_recebe_o_mesmo_404_generico(auth_client, factory, session):
    dono = factory.user()
    invasor = factory.user()

    login_directly(auth_client, session, dono)
    criado = auth_client.post("/api/payments", json={"plan_code": "weekly", "method": "pix"}).json()
    payment_id = criado["payment_id"]

    login_directly(auth_client, session, invasor)  # troca a sessão do TestClient para outro usuário
    resposta_invasor = auth_client.post(f"/api/payments/{payment_id}/submit", json=_pix_form())
    resposta_inexistente = auth_client.post("/api/payments/999999/submit", json=_pix_form())

    assert resposta_invasor.status_code == 404
    assert resposta_inexistente.status_code == 404
    assert resposta_invasor.json() == resposta_inexistente.json()  # mesmo corpo -- 404 genérico


def test_submit_metodo_incompativel_recebe_409_e_nao_chama_o_mercado_pago(auth_client, factory, session):
    login_directly(auth_client, session, factory.user())
    criado = auth_client.post("/api/payments", json={"plan_code": "weekly", "method": "pix"}).json()

    with patch("payments.client.create_payment") as mock_cliente:
        resposta = auth_client.post(f"/api/payments/{criado['payment_id']}/submit", json=_cartao_form())

    assert resposta.status_code == 409
    mock_cliente.assert_not_called()


def test_submit_pix_feliz_devolve_qr_code(auth_client, factory, session):
    login_directly(auth_client, session, factory.user())
    criado = auth_client.post("/api/payments", json={"plan_code": "weekly", "method": "pix"}).json()

    resposta_mp = _resposta_mp(
        status="pending",
        status_detail="pending_waiting_transfer",
        point_of_interaction={
            "transaction_data": {"qr_code": "00020126-copia-cola", "qr_code_base64": "aGVsbG8="}
        },
    )
    with patch("payments.client.create_payment", return_value=resposta_mp):
        resposta = auth_client.post(f"/api/payments/{criado['payment_id']}/submit", json=_pix_form())

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["status"] == "pending"
    assert corpo["qr_code"] == "00020126-copia-cola"
    assert corpo["qr_code_base64"] == "aGVsbG8="


def test_submit_cartao_feliz_devolve_approved(auth_client, factory, session):
    login_directly(auth_client, session, factory.user())
    criado = auth_client.post("/api/payments", json={"plan_code": "monthly", "method": "credit_card"}).json()

    with patch("payments.client.create_payment", return_value=_resposta_mp(status="approved", status_detail="accredited")):
        resposta = auth_client.post(f"/api/payments/{criado['payment_id']}/submit", json=_cartao_form())

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["status"] == "approved"
    assert corpo["status_detail"] == "accredited"
    assert corpo["qr_code"] is None


# ============================================================================ credenciais
def test_public_key_disponivel_ao_frontend(auth_client, factory, session, use_settings):
    use_settings(mp_public_key="TEST-public-key-visivel-ao-frontend")
    login_directly(auth_client, session, factory.user())

    resposta = auth_client.get("/api/payments/public-key")

    assert resposta.status_code == 200
    assert resposta.json()["public_key"] == "TEST-public-key-visivel-ao-frontend"


def test_access_token_nunca_aparece_em_nenhuma_resposta(auth_client, factory, session, use_settings):
    segredo = "access-token-que-jamais-pode-vazar-para-o-navegador"
    use_settings(mp_access_token=segredo, mp_public_key="chave-publica-ok")
    login_directly(auth_client, session, factory.user())

    criado = auth_client.post("/api/payments", json={"plan_code": "weekly", "method": "pix"})
    publica = auth_client.get("/api/payments/public-key")

    assert segredo not in criado.text
    assert segredo not in publica.text
