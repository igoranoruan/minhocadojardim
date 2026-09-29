import ast
import logging
import urllib.error
import urllib.request
from dataclasses import replace
from pathlib import Path

import pytest

from services.mailer import (
    BrevoEmailSender,
    ConsoleEmailSender,
    EmailConfigurationError,
    EmailMessage,
    EmailSendError,
    EmailSender,
    HttpEmailRequest,
    HttpEmailSender,
    UnavailableEmailSender,
    build_email_sender,
)

MENSAGEM = EmailMessage(to="ana@example.com", subject="Assunto", text="Seu código: 123456")


def test_console_sender_escreve_o_email_no_log(caplog):
    with caplog.at_level(logging.INFO, logger="minhoca"):
        ConsoleEmailSender().send(MENSAGEM)
    assert "123456" in caplog.text and "[EMAIL:console]" in caplog.text


def test_build_email_sender_em_desenvolvimento(cfg):
    assert isinstance(build_email_sender(replace(cfg, env="development", email_sender="console")), ConsoleEmailSender)


def test_console_sender_e_recusado_em_producao(cfg):
    with pytest.raises(EmailConfigurationError):
        build_email_sender(replace(cfg, env="production", email_sender="console"))


def test_producao_sem_provedor_configurado_falha_com_clareza(cfg):
    with pytest.raises(EmailConfigurationError):
        build_email_sender(replace(cfg, env="production", email_sender=""))


def test_nenhum_provedor_nao_escolhido_esta_acoplado(cfg):
    """"brevo" saiu desta lista de propósito (Etapa 11): é o único provedor concreto implementado
    hoje -- ver a suíte "Brevo" mais abaixo. Os demais continuam sem nenhum código acoplado."""
    for nome in ("resend", "sendgrid", "ses"):
        with pytest.raises(EmailConfigurationError):
            build_email_sender(replace(cfg, env="production", email_sender=nome))


def test_sender_indisponivel_sempre_falha():
    with pytest.raises(EmailConfigurationError):
        UnavailableEmailSender("sem provedor").send(MENSAGEM)


def test_interfaces_sao_abstratas():
    with pytest.raises(TypeError):
        EmailSender()
    with pytest.raises(TypeError):
        HttpEmailSender()


def test_mailer_nao_usa_smtp_nem_importa_provedor():
    fonte = Path(__file__).resolve().parent.parent / "services" / "mailer.py"
    importados = set()
    for no in ast.walk(ast.parse(fonte.read_text(encoding="utf-8"))):
        if isinstance(no, ast.Import):
            importados.update(a.name.split(".")[0] for a in no.names)
        elif isinstance(no, ast.ImportFrom) and no.module:
            importados.add(no.module.split(".")[0])
    assert not importados & {"smtplib", "resend", "brevo", "sendgrid", "boto3", "requests", "httpx"}


# ---------------------------------------------------------------- HttpEmailSender (base abstrata, provedor fictício)
class ProvedorFalso(HttpEmailSender):
    def __init__(self, url="https://api.provedor.example/v1/send"):
        self.url = url

    def build_request(self, message):
        return HttpEmailRequest(
            url=self.url,
            headers={"Authorization": "Bearer chave-secreta"},
            body={"to": message.to, "subject": message.subject, "text": message.text},
        )


class RespostaFalsa:
    def __init__(self, status=200):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_http_sender_faz_post_https_com_json_e_timeout(monkeypatch):
    chamadas = []

    def urlopen_falso(requisicao, timeout=None):
        chamadas.append((requisicao, timeout))
        return RespostaFalsa(202)

    monkeypatch.setattr(urllib.request, "urlopen", urlopen_falso)
    ProvedorFalso().send(MENSAGEM)

    (requisicao, timeout), = chamadas
    assert requisicao.get_method() == "POST"
    assert requisicao.full_url.startswith("https://")
    assert requisicao.get_header("Authorization") == "Bearer chave-secreta"
    assert requisicao.get_header("Content-type") == "application/json"
    assert b"ana@example.com" in requisicao.data
    assert timeout == 10.0


def test_http_sender_recusa_url_sem_https(monkeypatch):
    chamadas = []
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: chamadas.append(1))
    with pytest.raises(EmailConfigurationError):
        ProvedorFalso(url="http://api.provedor.example/v1/send").send(MENSAGEM)
    assert chamadas == []  # nada foi enviado


@pytest.mark.parametrize(
    "erro",
    [
        urllib.error.HTTPError("https://x", 500, "erro", None, None),
        urllib.error.URLError("sem rede"),
        TimeoutError("demorou"),
        ConnectionResetError("caiu"),
    ],
    ids=["http-500", "url-error", "timeout", "conexao"],
)
def test_http_sender_converte_falhas_em_emailsenderror_sem_vazar_dados(monkeypatch, erro):
    def urlopen_falso(requisicao, timeout=None):
        raise erro

    monkeypatch.setattr(urllib.request, "urlopen", urlopen_falso)
    with pytest.raises(EmailSendError) as capturado:
        ProvedorFalso().send(MENSAGEM)
    mensagem = str(capturado.value)
    assert "ana@example.com" not in mensagem and "123456" not in mensagem and "chave-secreta" not in mensagem


def test_http_sender_trata_status_fora_de_2xx_como_falha(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", lambda requisicao, timeout=None: RespostaFalsa(302))
    with pytest.raises(EmailSendError):
        ProvedorFalso().send(MENSAGEM)


# ---------------------------------------------------------------- BrevoEmailSender (Etapa 11, provedor real)
def _cfg_brevo(cfg, *, api_key="chave-fake-de-teste", email_from="contato@minhoca.example"):
    return replace(cfg, env="production", email_sender="brevo", brevo_api_key=api_key, email_from=email_from)


def test_email_sender_brevo_cria_o_sender_correto(cfg):
    sender = build_email_sender(_cfg_brevo(cfg))
    assert isinstance(sender, BrevoEmailSender)


def test_brevo_usa_a_url_oficial_da_api(cfg):
    sender = build_email_sender(_cfg_brevo(cfg))
    requisicao = sender.build_request(MENSAGEM)
    assert requisicao.url == "https://api.brevo.com/v3/smtp/email"


def test_brevo_envia_post_com_header_api_key(monkeypatch, cfg):
    chamadas = []

    def urlopen_falso(requisicao, timeout=None):
        chamadas.append(requisicao)
        return RespostaFalsa(201)

    monkeypatch.setattr(urllib.request, "urlopen", urlopen_falso)
    build_email_sender(_cfg_brevo(cfg, api_key="chave-123")).send(MENSAGEM)

    (requisicao,) = chamadas
    assert requisicao.get_method() == "POST"
    assert requisicao.get_header("Api-key") == "chave-123"
    assert requisicao.get_header("Content-type") == "application/json"


def test_brevo_usa_email_from_como_remetente_e_dados_da_mensagem(cfg):
    sender = build_email_sender(_cfg_brevo(cfg, email_from="contato@minhoca.example"))
    requisicao = sender.build_request(MENSAGEM)
    assert requisicao.body["sender"] == {"email": "contato@minhoca.example"}
    assert requisicao.body["to"] == [{"email": MENSAGEM.to}]
    assert requisicao.body["subject"] == MENSAGEM.subject
    assert requisicao.body["textContent"] == MENSAGEM.text


def test_brevo_nao_vaza_api_key_em_erro(monkeypatch, cfg):
    def urlopen_falso(requisicao, timeout=None):
        raise urllib.error.HTTPError("https://api.brevo.com/v3/smtp/email", 401, "unauthorized", None, None)

    monkeypatch.setattr(urllib.request, "urlopen", urlopen_falso)
    sender = build_email_sender(_cfg_brevo(cfg, api_key="chave-super-secreta-nao-pode-vazar"))
    with pytest.raises(EmailSendError) as capturado:
        sender.send(MENSAGEM)
    assert "chave-super-secreta-nao-pode-vazar" not in str(capturado.value)


def test_brevo_nao_importa_sdk_nem_outro_provedor():
    """Reforça, no escopo específico do Brevo, a mesma garantia estrutural de
    test_mailer_nao_usa_smtp_nem_importa_provedor: BrevoEmailSender não trouxe nenhum import novo
    (nem SDK do Brevo, nem httpx/requests) -- continua só stdlib (urllib), herdado de HttpEmailSender."""
    fonte = Path(__file__).resolve().parent.parent / "services" / "mailer.py"
    importados = set()
    for no in ast.walk(ast.parse(fonte.read_text(encoding="utf-8"))):
        if isinstance(no, ast.Import):
            importados.update(a.name.split(".")[0] for a in no.names)
        elif isinstance(no, ast.ImportFrom) and no.module:
            importados.add(no.module.split(".")[0])
    assert not importados & {"smtplib", "resend", "brevo", "sendgrid", "boto3", "requests", "httpx"}
