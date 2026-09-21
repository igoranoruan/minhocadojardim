"""Utilitários compartilhados pelos testes de autenticação (não são testes nem fixtures)."""
import re
from datetime import timedelta

from sqlalchemy import select

from database.models import AuthSession, LoginCode
from services.mailer import EmailMessage, EmailSender

BASE_URL = "http://localhost:8000"


class FakeEmailSender(EmailSender):
    """Guarda os e-mails em memória (nos testes, é daqui que se lê o código de login)."""

    def __init__(self) -> None:
        self.messages: list[EmailMessage] = []
        self.fail_with: Exception | None = None

    def send(self, message: EmailMessage) -> None:
        if self.fail_with is not None:
            raise self.fail_with
        self.messages.append(message)

    def codes_for(self, email: str) -> list[str]:
        return [re.search(r"\b(\d{6})\b", m.text).group(1) for m in self.messages if m.to == email]

    def last_code(self, email: str) -> str:
        return self.codes_for(email)[-1]


def shift_login_codes(session, seconds: float, email: str | None = None) -> None:
    """Envelhece os códigos (created_at e expires_at) para simular a passagem do tempo."""
    query = select(LoginCode)
    if email is not None:
        query = query.where(LoginCode.email == email)
    for row in session.execute(query).scalars().all():
        row.created_at = row.created_at - timedelta(seconds=seconds)
        row.expires_at = row.expires_at - timedelta(seconds=seconds)
    session.commit()


def shift_auth_sessions(session, seconds: float) -> None:
    """Envelhece as sessões (created_at, last_used_at e expires_at)."""
    for row in session.execute(select(AuthSession)).scalars().all():
        row.created_at = row.created_at - timedelta(seconds=seconds)
        row.last_used_at = row.last_used_at - timedelta(seconds=seconds)
        row.expires_at = row.expires_at - timedelta(seconds=seconds)
    session.commit()
