import re

from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def _html() -> str:
    return client.get("/").text


def test_modal_acesso_tem_os_tres_passos():
    html = _html()
    for elemento in ("modalAcesso", "acessoPassoEmail", "acessoPassoCodigo", "acessoPassoConectado", "userEmail", "userCodigo"):
        assert f'id="{elemento}"' in html, elemento
    for texto in ("Enviar código", "Entrar", "Reenviar código", "Trocar e-mail", "Sair"):
        assert texto in html, texto
    assert 'autocomplete="one-time-code"' in html and 'inputmode="numeric"' in html


def test_o_frontend_usa_os_endpoints_de_autenticacao():
    html = _html()
    assert "/api/auth/${caminho}" in html
    for caminho in ('chamarAuth("me")', 'chamarAuth("request-code"', 'chamarAuth("verify-code"', 'chamarAuth("logout"'):
        assert caminho in html, caminho
    assert 'credentials: "same-origin"' in html


def test_a_sessao_e_descoberta_pelo_me_ao_carregar():
    html = _html()
    corpo = re.search(r'addEventListener\("DOMContentLoaded", \(\) => \{(.*?)\n    \}\);', html, re.S).group(1)
    assert "carregarSessao()" in corpo
    assert "localStorage" not in corpo


def test_e_mail_nao_e_mais_autenticacao_nem_fica_salvo_no_navegador():
    html = _html()
    assert "localStorage" not in html and "sessionStorage" not in html
    assert "/api/check-email" not in html and "/api/subscription?email=" not in html
    assert "ativarEmail" not in html


def test_o_frontend_nao_le_nem_guarda_o_token_da_sessao():
    html = _html()
    assert "document.cookie" not in html  # o cookie é HttpOnly: só o servidor o vê


def test_mensagens_do_modal_nao_usam_innerhtml():
    html = _html()
    inicio = html.index("function mostrarMensagemAcesso")
    fim = html.index("function mostrarPassoAcesso")
    assert "innerHTML" not in html[inicio:fim]
    assert "textContent" in html[inicio:fim]
