"""Envio de e-mail atrás de uma interface (EmailSender).

- ConsoleEmailSender: só para desenvolvimento (mostra o e-mail no log). É RECUSADO em produção,
  porque o e-mail contém o código de login e código nunca pode ser registrado em produção.
- HttpEmailSender: base abstrata para provedores que falam HTTPS. Uma subclasse só implementa
  build_request(); toda a mecânica de POST/JSON/timeout/tratamento de erro já mora aqui.
- BrevoEmailSender: o provedor de produção escolhido (Etapa 11), via API transacional oficial do
  Brevo (https://api.brevo.com/v3/smtp/email). api_key e remetente vêm de Settings
  (brevo_api_key/email_from) -- nunca hardcoded, nunca lidos de os.getenv() diretamente aqui.
- Nenhuma dependência nova: a chamada HTTPS usa só a biblioteca padrão (urllib), não usamos SMTP
  nem o SDK oficial do Brevo.
"""
import json
import logging
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass

from config import Settings

logger = logging.getLogger("minhoca")


class EmailSendError(Exception):
    """Falha ao enviar o e-mail (a mensagem nunca deve conter e-mail nem código)."""


class EmailConfigurationError(EmailSendError):
    """Envio de e-mail não configurado corretamente para este ambiente."""


@dataclass(frozen=True)
class EmailMessage:
    to: str
    subject: str
    text: str


class EmailSender(ABC):
    @abstractmethod
    def send(self, message: EmailMessage) -> None:
        """Envia a mensagem ou levanta EmailSendError."""


class ConsoleEmailSender(EmailSender):
    """Desenvolvimento: escreve o e-mail no log (é aqui que você vê o código de login)."""

    def send(self, message: EmailMessage) -> None:
        logger.info("[EMAIL:console] Para: %s | Assunto: %s\n%s", message.to, message.subject, message.text)


class UnavailableEmailSender(EmailSender):
    """Usado quando não há sender configurado: toda tentativa de envio falha com clareza."""

    def __init__(self, reason: str) -> None:
        self._reason = reason

    def send(self, message: EmailMessage) -> None:
        raise EmailConfigurationError(self._reason)


@dataclass(frozen=True)
class HttpEmailRequest:
    url: str
    headers: dict[str, str]
    body: dict


class HttpEmailSender(EmailSender, ABC):
    """Base para provedores que recebem o e-mail por uma requisição HTTPS (POST com JSON)."""

    timeout_seconds: float = 10.0

    @abstractmethod
    def build_request(self, message: EmailMessage) -> HttpEmailRequest:
        """Monta URL (https), cabeçalhos (ex.: autorização) e corpo no formato do provedor."""

    def send(self, message: EmailMessage) -> None:
        request = self.build_request(message)
        if not request.url.lower().startswith("https://"):
            raise EmailConfigurationError("A URL do provedor de e-mail precisa usar HTTPS.")
        http_request = urllib.request.Request(
            request.url,
            data=json.dumps(request.body).encode("utf-8"),
            headers={"Content-Type": "application/json", **request.headers},
            method="POST",
        )
        try:
            with urllib.request.urlopen(http_request, timeout=self.timeout_seconds) as response:
                status = response.status
        except urllib.error.HTTPError as exc:
            raise EmailSendError(f"O provedor de e-mail respondeu HTTP {exc.code}.") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise EmailSendError(f"Falha na chamada HTTPS ao provedor de e-mail ({exc.__class__.__name__}).") from None
        if not 200 <= status < 300:
            raise EmailSendError(f"O provedor de e-mail respondeu HTTP {status}.")


class BrevoEmailSender(HttpEmailSender):
    """Provedor de produção via API transacional oficial do Brevo (POST JSON, sem SDK).

    api_key e sender_email vêm de Settings (cfg.brevo_api_key/cfg.email_from), passados pelo
    construtor -- esta classe nunca lê variável de ambiente diretamente. A base HttpEmailSender já
    garante que nem a api_key nem o corpo da requisição aparecem em EmailSendError/logs de falha
    (só o código HTTP ou a classe da exceção de rede)."""

    _URL = "https://api.brevo.com/v3/smtp/email"

    def __init__(self, *, api_key: str, sender_email: str) -> None:
        self._api_key = api_key
        self._sender_email = sender_email

    def build_request(self, message: EmailMessage) -> HttpEmailRequest:
        return HttpEmailRequest(
            url=self._URL,
            headers={"api-key": self._api_key},
            body={
                # "name" (02/10/2026): sem isso, o Brevo manda só o e-mail no campo "sender", e
                # clientes de e-mail (confirmado no Gmail por print do Igor) mostram o REMETENTE
                # como "noreply" (o começo literal de noreply@klango.site) em vez do nome da marca
                # -- mesmo o nome "KLANGO MP4" já estando configurado no painel do Brevo (esse nome
                # é só para a interface do painel, não é enviado automaticamente na API transacional
                # se a chamada não pedir). Hardcoded (mesmo padrão já usado no assunto/corpo da
                # mensagem, em _login_code_message) em vez de um campo novo em Settings -- a marca
                # não é um dado de configuração por ambiente.
                "sender": {"email": self._sender_email, "name": "KLANGO.MP4"},
                "to": [{"email": message.to}],
                "subject": message.subject,
                "textContent": message.text,
            },
        )


def build_email_sender(cfg: Settings) -> EmailSender:
    """Escolhe o sender do ambiente. Levanta EmailConfigurationError se não houver um válido."""
    if cfg.email_sender == "console":
        if cfg.is_production:
            raise EmailConfigurationError("O ConsoleEmailSender não pode ser usado em produção.")
        return ConsoleEmailSender()
    if cfg.email_sender == "brevo":
        return BrevoEmailSender(api_key=cfg.brevo_api_key, sender_email=cfg.email_from)
    raise EmailConfigurationError("Nenhum provedor de e-mail de produção está configurado.")
