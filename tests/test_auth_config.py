import pytest

import config
from config import (
    DEV_AUTH_SECRET_KEY,
    DEV_IP_HASH_SECRET,
    MIN_SECRET_LENGTH,
    ORIGIN_CHECK_EXEMPT_PREFIXES,
    load_settings,
)

VARIAVEIS = (
    "ENV", "AUTH_SECRET_KEY", "IP_HASH_SECRET", "EMAIL_SENDER", "AUTH_CODE_TTL_SECONDS",
    "AUTH_CODE_MAX_ATTEMPTS", "AUTH_CODE_MIN_INTERVAL_SECONDS", "AUTH_CODE_MAX_PER_EMAIL_PER_HOUR",
    "AUTH_CODE_MAX_PER_IP_PER_HOUR", "AUTH_SESSION_TTL_DAYS",
)
SEGREDO_A = "a" * MIN_SECRET_LENGTH
SEGREDO_B = "b" * MIN_SECRET_LENGTH


@pytest.fixture(autouse=True)
def ambiente_limpo(monkeypatch):
    for nome in VARIAVEIS:
        monkeypatch.delenv(nome, raising=False)


def test_producao_sem_auth_secret_key_nao_sobe(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("IP_HASH_SECRET", SEGREDO_B)
    with pytest.raises(RuntimeError, match="AUTH_SECRET_KEY"):
        load_settings()


def test_producao_sem_ip_hash_secret_nao_sobe(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("AUTH_SECRET_KEY", SEGREDO_A)
    with pytest.raises(RuntimeError, match="IP_HASH_SECRET"):
        load_settings()


def test_producao_rejeita_segredo_curto(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("AUTH_SECRET_KEY", "curto")
    monkeypatch.setenv("IP_HASH_SECRET", SEGREDO_B)
    with pytest.raises(RuntimeError, match="AUTH_SECRET_KEY"):
        load_settings()


def test_producao_exige_segredos_diferentes(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("AUTH_SECRET_KEY", SEGREDO_A)
    monkeypatch.setenv("IP_HASH_SECRET", SEGREDO_A)
    with pytest.raises(RuntimeError, match="DIFERENTES"):
        load_settings()


def test_producao_valida_usa_cookie_host_secure_e_nao_tem_sender_padrao(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("AUTH_SECRET_KEY", SEGREDO_A)
    monkeypatch.setenv("IP_HASH_SECRET", SEGREDO_B)
    s = load_settings()
    assert s.is_production and s.session_cookie_secure
    assert s.session_cookie_name == "__Host-minhoca_session"
    assert s.email_sender == ""  # o provedor de produção ainda não foi escolhido
    assert not s.using_dev_secrets


def test_desenvolvimento_usa_segredos_fixos_e_cookie_simples():
    s = load_settings()
    assert not s.is_production and not s.session_cookie_secure
    assert s.session_cookie_name == "minhoca_session"
    assert (s.auth_secret_key, s.ip_hash_secret) == (DEV_AUTH_SECRET_KEY, DEV_IP_HASH_SECRET)
    assert DEV_AUTH_SECRET_KEY != DEV_IP_HASH_SECRET  # segredos separados também em desenvolvimento
    assert s.using_dev_secrets and s.email_sender == "console"


def test_parametros_padrao_aprovados():
    s = load_settings()
    assert s.login_code_ttl_seconds == 600
    assert s.login_code_max_attempts == 5
    assert s.login_code_min_interval_seconds == 60
    assert s.login_code_max_per_email_per_hour == 5
    assert s.login_code_max_per_ip_per_hour == 20
    assert s.session_ttl_days == 30
    assert config.LOGIN_CODE_LENGTH == 6 and config.SESSION_TOKEN_BYTES == 32


def test_parametros_sao_configuraveis_e_validados(monkeypatch):
    monkeypatch.setenv("AUTH_CODE_TTL_SECONDS", "120")
    monkeypatch.setenv("AUTH_CODE_MAX_PER_IP_PER_HOUR", "7")
    s = load_settings()
    assert (s.login_code_ttl_seconds, s.login_code_max_per_ip_per_hour) == (120, 7)
    monkeypatch.setenv("AUTH_CODE_MAX_ATTEMPTS", "0")
    with pytest.raises(ValueError):
        load_settings()


def test_repr_das_configuracoes_nao_vaza_segredos(monkeypatch):
    monkeypatch.setenv("AUTH_SECRET_KEY", SEGREDO_A)
    assert SEGREDO_A not in repr(load_settings())


def test_get_settings_le_o_valor_atual(monkeypatch):
    novo = load_settings()
    monkeypatch.setattr(config, "settings", novo)
    assert config.get_settings() is novo


def test_prefixo_de_webhook_esta_isento_da_checagem_de_origem():
    assert "/api/webhooks/" in ORIGIN_CHECK_EXEMPT_PREFIXES
