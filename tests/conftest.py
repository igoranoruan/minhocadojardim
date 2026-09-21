"""Fixtures dos testes de banco.

Cada teste recebe um SQLite temporário criado pelas MIGRATIONS reais (alembic upgrade),
não por create_all. Assim os testes validam o mesmo schema que vai para produção.
"""
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

import config
from database.models import Batch, Entitlement, Generation, Payment, User
from database.session import create_db_engine, get_session
from database.types import utcnow
from main import app
from routes.deps import get_email_sender
from helpers_auth import BASE_URL, FakeEmailSender

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def make_alembic_config(db_url: str) -> Config:
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", db_url.replace("%", "%%"))
    cfg.attributes["configure_logger"] = False
    return cfg


@pytest.fixture()
def db_url(tmp_path) -> str:
    return f"sqlite:///{(tmp_path / 'teste.db').as_posix()}"


@pytest.fixture()
def alembic_cfg(db_url) -> Config:
    return make_alembic_config(db_url)


@pytest.fixture()
def engine(db_url, alembic_cfg):
    """Engine de um banco já migrado até o head."""
    command.upgrade(alembic_cfg, "head")
    eng = create_db_engine(db_url)
    yield eng
    eng.dispose()


@pytest.fixture()
def session(engine):
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    with maker() as s:
        yield s
        s.rollback()


@pytest.fixture()
def assert_rejected(session):
    """Garante que a operação é rejeitada pelo banco (IntegrityError) e limpa a transação."""

    def _check(operation) -> None:
        with pytest.raises(IntegrityError):
            operation()
        session.rollback()

    return _check


class Factory:
    """Cria registros válidos (com commit). Cada método aceita overrides por keyword."""

    def __init__(self, session) -> None:
        self.session = session
        self._n = 0

    def _next(self) -> int:
        self._n += 1
        return self._n

    def _persist(self, obj):
        self.session.add(obj)
        self.session.commit()
        return obj

    def user(self, email: str | None = None) -> User:
        return self._persist(User(email=email or f"user{self._next()}@example.com"))

    def payment(self, user: User | None = None, **overrides) -> Payment:
        user = user or self.user()
        data = dict(
            user_id=user.id,
            plan_code="weekly",
            amount_cents=990,
            method="pix",
            status="pending",
            external_reference=f"ref-{self._next()}",
        )
        data.update(overrides)
        return self._persist(Payment(**data))

    def entitlement(self, payment: Payment | None = None, **overrides) -> Entitlement:
        payment = payment or self.payment()
        now = utcnow()
        data = dict(
            user_id=payment.user_id,
            plan_code=payment.plan_code,
            payment_id=payment.id,
            duration_days=7,
            starts_at=now,
            expires_at=now + timedelta(days=7),
            status="granted",
        )
        data.update(overrides)
        return self._persist(Entitlement(**data))

    def batch(self, user: User | None = None, **overrides) -> Batch:
        user = user or self.user()
        data = dict(user_id=user.id, request_id=f"batch-{self._next()}", item_count=3)
        data.update(overrides)
        return self._persist(Batch(**data))

    def generation(self, user: User | None = None, **overrides) -> Generation:
        user = user or self.user()
        data = dict(
            user_id=user.id,
            request_id=f"req-{self._next()}",
            plan_code="free",
            status="reserved",
            period_day=date(2026, 9, 21),
            period_week=date(2026, 9, 21),
        )
        data.update(overrides)
        return self._persist(Generation(**data))


@pytest.fixture()
def factory(session) -> Factory:
    return Factory(session)


# ============================================================================ Etapa 3: autenticação
@pytest.fixture()
def fake_sender() -> FakeEmailSender:
    return FakeEmailSender()


@pytest.fixture()
def cfg():
    """Configuração de desenvolvimento com os valores padrão aprovados."""
    return replace(config.get_settings(), env="development", email_sender="console")


@pytest.fixture()
def maker(engine):
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture()
def use_settings(monkeypatch):
    """Troca a configuração global (lida no momento do uso pelo middleware, rotas e serviços)."""

    def _apply(**overrides):
        new = replace(config.get_settings(), **overrides)
        monkeypatch.setattr(config, "settings", new)
        return new

    return _apply


@pytest.fixture()
def auth_client(maker, fake_sender):
    """Cliente HTTP com banco de teste e sender falso. Já manda o Origin correto."""

    def _session():
        s = maker()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_email_sender] = lambda: fake_sender
    client = TestClient(app, base_url=BASE_URL)
    client.headers.update({"Origin": BASE_URL})
    yield client
    app.dependency_overrides.pop(get_session, None)
    app.dependency_overrides.pop(get_email_sender, None)
