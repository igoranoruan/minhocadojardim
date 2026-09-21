import hashlib
import logging
from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import select, text

import services.auth as auth_service
from helpers_auth import shift_auth_sessions, shift_login_codes
from database.models import AuthSession, LoginCode, User
from services.auth import (
    AuthError,
    EmailUnavailableError,
    InvalidCodeError,
    InvalidEmailError,
    RateLimitedError,
    authenticate_session,
    request_login_code,
    revoke_session,
    verify_login_code,
)
from services.mailer import EmailSendError
from utils.security import hash_login_code

EMAIL = "ana@example.com"
IP = "203.0.113.10"


def _pedir(session, sender, cfg, email=EMAIL, ip=IP):
    return request_login_code(session, email=email, ip=ip, sender=sender, cfg=cfg)


def _linhas(session):
    session.expire_all()
    return session.execute(select(LoginCode).order_by(LoginCode.id)).scalars().all()


def _abertos(session, email=EMAIL):
    return session.execute(
        text("SELECT COUNT(*) FROM login_codes WHERE email = :e AND active_slot = 1"), {"e": email}
    ).scalar_one()


def _errado(certo: str) -> str:
    return "000000" if certo != "000000" else "111111"


def _contagem(session, tabela):
    return session.execute(text(f"SELECT COUNT(*) FROM {tabela}")).scalar_one()


# ============================================================================ pedir código
def test_pedir_codigo_envia_email_e_guarda_somente_o_hash(session, fake_sender, cfg):
    emitido = _pedir(session, fake_sender, cfg, email="  Ana@Example.COM ")
    assert (emitido.expires_in, emitido.resend_after) == (600, 60)

    assert len(fake_sender.messages) == 1
    codigo = fake_sender.last_code(EMAIL)
    assert len(codigo) == 6 and codigo.isdigit()

    (linha,) = _linhas(session)
    assert linha.email == EMAIL  # normalizado com strip().lower()
    assert linha.code_hash == hash_login_code(EMAIL, codigo, cfg.auth_secret_key)
    assert linha.code_hash != codigo
    assert linha.code_hash != hashlib.sha256(codigo.encode()).hexdigest()
    assert linha.active_slot == 1 and linha.attempts == 0 and linha.closed_at is None
    assert linha.ip_hash is not None and IP not in linha.ip_hash
    assert 590 <= (linha.expires_at - linha.created_at).total_seconds() <= 610


def test_o_email_enviado_contem_o_codigo_e_a_validade(session, fake_sender, cfg):
    _pedir(session, fake_sender, cfg)
    mensagem = fake_sender.messages[0]
    assert mensagem.to == EMAIL
    assert fake_sender.last_code(EMAIL) in mensagem.text
    assert "10 minuto" in mensagem.text


@pytest.mark.parametrize("email", ["", "abc", "a@b", "a b@c.com", "a@c.com\nBcc: x@y.com", None, 123])
def test_email_invalido_e_rejeitado_sem_enviar_nada(session, fake_sender, cfg, email):
    with pytest.raises(InvalidEmailError) as erro:
        _pedir(session, fake_sender, cfg, email=email)
    assert erro.value.status_code == 400 and erro.value.code == "invalid_email"
    assert fake_sender.messages == [] and _contagem(session, "login_codes") == 0


def test_novo_codigo_invalida_o_anterior(session, fake_sender, cfg):
    _pedir(session, fake_sender, cfg)
    primeiro = fake_sender.last_code(EMAIL)
    shift_login_codes(session, 61)  # passa o intervalo mínimo de 60 s
    _pedir(session, fake_sender, cfg)
    segundo = fake_sender.last_code(EMAIL)

    antigo, novo = _linhas(session)
    assert (antigo.close_reason, antigo.active_slot) == ("superseded", None) and antigo.closed_at is not None
    assert novo.active_slot == 1 and novo.close_reason is None
    assert _abertos(session) == 1

    if primeiro != segundo:
        with pytest.raises(InvalidCodeError):
            verify_login_code(session, email=EMAIL, code=primeiro, cfg=cfg)
    assert verify_login_code(session, email=EMAIL, code=segundo, cfg=cfg).user.email == EMAIL


def test_intervalo_minimo_de_60_segundos_entre_codigos(session, fake_sender, cfg):
    _pedir(session, fake_sender, cfg)
    with pytest.raises(RateLimitedError) as erro:
        _pedir(session, fake_sender, cfg)
    assert erro.value.status_code == 429 and erro.value.code == "rate_limited"
    assert 1 <= erro.value.retry_after <= 60
    assert len(fake_sender.messages) == 1  # o segundo pedido não enviou e-mail
    assert _contagem(session, "login_codes") == 1

    shift_login_codes(session, 61)
    _pedir(session, fake_sender, cfg)  # passado o intervalo, funciona
    assert len(fake_sender.messages) == 2


def test_limite_de_5_codigos_por_hora_por_email(session, fake_sender, cfg):
    rapido = replace(cfg, login_code_min_interval_seconds=0)
    for _ in range(5):
        _pedir(session, fake_sender, rapido)
    with pytest.raises(RateLimitedError) as erro:
        _pedir(session, fake_sender, rapido)
    assert 1 <= erro.value.retry_after <= 3600
    assert len(fake_sender.messages) == 5

    shift_login_codes(session, 2 * 3600)  # a janela de 1 hora andou
    _pedir(session, fake_sender, rapido)
    assert len(fake_sender.messages) == 6


def test_limite_de_20_codigos_por_hora_por_ip(session, fake_sender, cfg):
    rapido = replace(cfg, login_code_min_interval_seconds=0)
    assert rapido.login_code_max_per_ip_per_hour == 20
    for i in range(20):
        _pedir(session, fake_sender, rapido, email=f"pessoa{i}@example.com")
    with pytest.raises(RateLimitedError) as erro:
        _pedir(session, fake_sender, rapido, email="pessoa20@example.com")
    assert 1 <= erro.value.retry_after <= 3600
    assert len(fake_sender.messages) == 20

    _pedir(session, fake_sender, rapido, email="pessoa20@example.com", ip="198.51.100.7")  # outro IP passa


def test_limite_por_ip_configuravel_e_independente_do_limite_por_email(session, fake_sender, cfg):
    pequeno = replace(cfg, login_code_min_interval_seconds=0, login_code_max_per_ip_per_hour=2)
    _pedir(session, fake_sender, pequeno, email="a@example.com")
    _pedir(session, fake_sender, pequeno, email="b@example.com")
    with pytest.raises(RateLimitedError):
        _pedir(session, fake_sender, pequeno, email="c@example.com")


def test_falha_do_sender_devolve_503_generico_e_fecha_o_codigo(session, fake_sender, cfg):
    fake_sender.fail_with = EmailSendError("erro interno do provedor com detalhes: chave=abc123")
    with pytest.raises(EmailUnavailableError) as erro:
        _pedir(session, fake_sender, cfg)
    assert erro.value.status_code == 503 and erro.value.code == "email_unavailable"
    assert "abc123" not in erro.value.detail and "provedor" not in erro.value.detail

    (linha,) = _linhas(session)
    assert (linha.close_reason, linha.active_slot) == ("send_failed", None)
    assert _abertos(session) == 0

    # falha de envio não conta nos limites: dá para tentar de novo na hora
    fake_sender.fail_with = None
    _pedir(session, fake_sender, cfg)
    assert len(fake_sender.messages) == 1 and _abertos(session) == 1


def test_qualquer_excecao_do_sender_vira_503(session, fake_sender, cfg):
    fake_sender.fail_with = RuntimeError("bug qualquer")
    with pytest.raises(EmailUnavailableError):
        _pedir(session, fake_sender, cfg)
    assert _abertos(session) == 0


def test_pedir_codigo_nao_revela_se_o_email_existe(session, fake_sender, cfg, factory):
    factory.user(email="existe@example.com")
    usuarios_antes = _contagem(session, "users")
    a = _pedir(session, fake_sender, cfg, email="existe@example.com")
    b = _pedir(session, fake_sender, cfg, email="novo@example.com")
    assert a == b  # mesma resposta
    assert _contagem(session, "users") == usuarios_antes  # e nenhum usuário foi criado


# ============================================================================ verificar código
def test_usuario_so_e_criado_apos_o_codigo_correto(session, fake_sender, cfg):
    _pedir(session, fake_sender, cfg)
    assert _contagem(session, "users") == 0
    with pytest.raises(InvalidCodeError):
        verify_login_code(session, email=EMAIL, code=_errado(fake_sender.last_code(EMAIL)), cfg=cfg)
    assert _contagem(session, "users") == 0

    resultado = verify_login_code(session, email=EMAIL, code=fake_sender.last_code(EMAIL), cfg=cfg)
    assert _contagem(session, "users") == 1
    assert resultado.user.email == EMAIL and resultado.user.email_verified_at is not None


def test_codigo_valido_cria_sessao_de_30_dias_fixos_e_fecha_o_codigo(session, fake_sender, cfg):
    _pedir(session, fake_sender, cfg)
    resultado = verify_login_code(session, email=EMAIL, code=fake_sender.last_code(EMAIL), cfg=cfg)

    assert len(resultado.token) == 43  # 256 bits em base64 url-safe
    session.expire_all()
    (sessao,) = session.execute(select(AuthSession)).scalars().all()
    assert sessao.token_hash == hashlib.sha256(resultado.token.encode()).hexdigest()
    assert sessao.token_hash != resultado.token
    assert sessao.expires_at - sessao.created_at == timedelta(days=30)
    assert sessao.revoked_at is None and sessao.user_id == resultado.user.id
    assert resultado.expires_at == sessao.expires_at

    (codigo,) = _linhas(session)
    assert (codigo.close_reason, codigo.active_slot) == ("verified", None)
    assert _abertos(session) == 0


def test_o_token_nao_e_guardado_em_nenhuma_coluna(session, fake_sender, cfg):
    _pedir(session, fake_sender, cfg)
    token = verify_login_code(session, email=EMAIL, code=fake_sender.last_code(EMAIL), cfg=cfg).token
    for tabela in ("auth_sessions", "login_codes", "users"):
        linhas = session.execute(text(f"SELECT * FROM {tabela}")).all()
        assert all(token not in str(valor) for linha in linhas for valor in linha)


def test_codigo_errado_conta_tentativa_e_mantem_o_codigo_aberto(session, fake_sender, cfg):
    _pedir(session, fake_sender, cfg)
    certo = fake_sender.last_code(EMAIL)
    errado = _errado(certo)
    with pytest.raises(InvalidCodeError):
        verify_login_code(session, email=EMAIL, code=errado, cfg=cfg)
    (linha,) = _linhas(session)
    assert linha.attempts == 1 and linha.active_slot == 1


def test_zeros_a_esquerda_funcionam_de_ponta_a_ponta(session, fake_sender, cfg, monkeypatch):
    monkeypatch.setattr("utils.security.secrets.randbelow", lambda limite: 7)
    _pedir(session, fake_sender, cfg)
    assert fake_sender.last_code(EMAIL) == "000007"
    with pytest.raises(InvalidCodeError):
        verify_login_code(session, email=EMAIL, code="7", cfg=cfg)  # formato inválido, e não vira número
    assert verify_login_code(session, email=EMAIL, code="000007", cfg=cfg).user.email == EMAIL


def test_codigo_expirado_e_rejeitado(session, fake_sender, cfg):
    _pedir(session, fake_sender, cfg)
    codigo = fake_sender.last_code(EMAIL)
    shift_login_codes(session, 601)  # passou os 10 minutos
    with pytest.raises(InvalidCodeError):
        verify_login_code(session, email=EMAIL, code=codigo, cfg=cfg)
    assert _contagem(session, "users") == 0 and _contagem(session, "auth_sessions") == 0


def test_codigo_esgota_apos_5_tentativas_erradas(session, fake_sender, cfg):
    _pedir(session, fake_sender, cfg)
    certo = fake_sender.last_code(EMAIL)
    errado = _errado(certo)
    for _ in range(5):
        with pytest.raises(InvalidCodeError):
            verify_login_code(session, email=EMAIL, code=errado, cfg=cfg)
    (linha,) = _linhas(session)
    assert (linha.attempts, linha.close_reason, linha.active_slot) == (5, "exhausted", None)

    with pytest.raises(InvalidCodeError):  # nem o código certo vale depois de esgotado
        verify_login_code(session, email=EMAIL, code=certo, cfg=cfg)
    assert _contagem(session, "auth_sessions") == 0

    shift_login_codes(session, 61)
    _pedir(session, fake_sender, cfg)  # um novo código resolve
    assert verify_login_code(session, email=EMAIL, code=fake_sender.last_code(EMAIL), cfg=cfg).user.email == EMAIL


def test_a_quinta_tentativa_ainda_pode_acertar(session, fake_sender, cfg):
    _pedir(session, fake_sender, cfg)
    certo = fake_sender.last_code(EMAIL)
    errado = _errado(certo)
    for _ in range(4):
        with pytest.raises(InvalidCodeError):
            verify_login_code(session, email=EMAIL, code=errado, cfg=cfg)
    assert verify_login_code(session, email=EMAIL, code=certo, cfg=cfg).user.email == EMAIL


def test_um_codigo_ja_usado_nao_vale_de_novo(session, fake_sender, cfg):
    _pedir(session, fake_sender, cfg)
    codigo = fake_sender.last_code(EMAIL)
    verify_login_code(session, email=EMAIL, code=codigo, cfg=cfg)
    with pytest.raises(InvalidCodeError):
        verify_login_code(session, email=EMAIL, code=codigo, cfg=cfg)
    assert _contagem(session, "auth_sessions") == 1


def test_mensagem_indistinguivel_para_todos_os_motivos(session, fake_sender, cfg):
    def falha(**kw):
        with pytest.raises(AuthError) as erro:
            verify_login_code(session, cfg=cfg, **kw)
        e = erro.value
        return (e.status_code, e.code, e.detail, e.retry_after)

    # sem nenhum código pedido para este e-mail
    inexistente = falha(email="ninguem@example.com", code="123456")

    _pedir(session, fake_sender, cfg)
    certo = fake_sender.last_code(EMAIL)
    errado = _errado(certo)
    incorreto = falha(email=EMAIL, code=errado)
    formato = falha(email=EMAIL, code="12ab")

    for _ in range(4):  # esgota (já teve 1 tentativa)
        falha(email=EMAIL, code=errado)
    esgotado = falha(email=EMAIL, code=certo)

    shift_login_codes(session, 61)
    _pedir(session, fake_sender, cfg)
    novo = fake_sender.last_code(EMAIL)
    shift_login_codes(session, 601)
    expirado = falha(email=EMAIL, code=novo)

    assert inexistente == incorreto == formato == esgotado == expirado
    assert inexistente[:2] == (400, "invalid_code")


def test_verificar_sem_codigo_aberto_nao_cria_usuario(session, cfg):
    with pytest.raises(InvalidCodeError):
        verify_login_code(session, email=EMAIL, code="123456", cfg=cfg)
    assert _contagem(session, "users") == 0


def test_email_de_verificacao_e_normalizado(session, fake_sender, cfg):
    _pedir(session, fake_sender, cfg)
    resultado = verify_login_code(session, email="  ANA@Example.com ", code=fake_sender.last_code(EMAIL), cfg=cfg)
    assert resultado.user.email == EMAIL


def test_email_invalido_na_verificacao(session, cfg):
    with pytest.raises(InvalidEmailError):
        verify_login_code(session, email="abc", code="123456", cfg=cfg)


def test_email_verified_at_e_gravado_uma_unica_vez(session, fake_sender, cfg):
    _pedir(session, fake_sender, cfg)
    primeiro = verify_login_code(session, email=EMAIL, code=fake_sender.last_code(EMAIL), cfg=cfg)
    verificado_em = primeiro.user.email_verified_at
    assert verificado_em is not None

    shift_login_codes(session, 61)
    _pedir(session, fake_sender, cfg)
    segundo = verify_login_code(session, email=EMAIL, code=fake_sender.last_code(EMAIL), cfg=cfg)
    session.expire_all()
    assert segundo.user.id == primeiro.user.id  # mesmo usuário, sem duplicar
    assert session.get(User, primeiro.user.id).email_verified_at == verificado_em
    assert _contagem(session, "users") == 1


def test_usuario_existente_sem_verificacao_passa_a_ser_verificado(session, fake_sender, cfg, factory):
    existente = factory.user(email=EMAIL)
    assert existente.email_verified_at is None
    _pedir(session, fake_sender, cfg)
    verify_login_code(session, email=EMAIL, code=fake_sender.last_code(EMAIL), cfg=cfg)
    session.expire_all()
    assert session.get(User, existente.id).email_verified_at is not None
    assert _contagem(session, "users") == 1


# ============================================================================ sessão
def _logar(session, sender, cfg, email=EMAIL):
    shift_login_codes(session, 61, email=email)
    _pedir(session, sender, cfg, email=email)
    return verify_login_code(session, email=email, code=sender.last_code(email), cfg=cfg)


def test_authenticate_devolve_o_usuario_da_sessao(session, fake_sender, cfg):
    login = _logar(session, fake_sender, cfg)
    assert authenticate_session(session, login.token).id == login.user.id
    for invalido in (None, "", "token-que-nao-existe", "x" * 600):
        assert authenticate_session(session, invalido) is None


def test_sessao_expira_na_data_original(session, fake_sender, cfg):
    login = _logar(session, fake_sender, cfg)
    shift_auth_sessions(session, 29 * 86400)
    assert authenticate_session(session, login.token) is not None  # 29 dias: ainda vale
    shift_auth_sessions(session, 2 * 86400)
    assert authenticate_session(session, login.token) is None  # passou de 30 dias


def test_a_sessao_nao_e_renovada_a_cada_requisicao(session, fake_sender, cfg, monkeypatch):
    monkeypatch.setattr(auth_service, "SESSION_LAST_USED_UPDATE_INTERVAL_SECONDS", 0)  # força atualizar last_used_at
    login = _logar(session, fake_sender, cfg)
    session.expire_all()
    (antes,) = session.execute(select(AuthSession)).scalars().all()
    expira_antes, criada_antes = antes.expires_at, antes.created_at

    for _ in range(3):
        assert authenticate_session(session, login.token) is not None

    session.expire_all()
    (depois,) = session.execute(select(AuthSession)).scalars().all()
    assert depois.expires_at == expira_antes and depois.created_at == criada_antes  # sem sliding session
    assert depois.last_used_at >= antes.last_used_at


def test_last_used_at_so_e_atualizado_de_vez_em_quando(session, fake_sender, cfg):
    login = _logar(session, fake_sender, cfg)
    session.expire_all()
    (sessao,) = session.execute(select(AuthSession)).scalars().all()
    inicial = sessao.last_used_at

    authenticate_session(session, login.token)
    session.expire_all()
    assert session.execute(select(AuthSession.last_used_at)).scalar_one() == inicial  # dentro do intervalo: sem escrita

    shift_auth_sessions(session, 2 * 3600)
    velho = session.execute(select(AuthSession.last_used_at)).scalar_one()
    authenticate_session(session, login.token)
    session.expire_all()
    assert session.execute(select(AuthSession.last_used_at)).scalar_one() > velho


def test_revogacao_e_logout_repetido(session, fake_sender, cfg):
    login = _logar(session, fake_sender, cfg)
    assert revoke_session(session, login.token) is True
    assert authenticate_session(session, login.token) is None
    assert revoke_session(session, login.token) is False  # repetir é seguro
    assert revoke_session(session, "token-inexistente") is False
    assert revoke_session(session, None) is False


def test_cada_login_gera_um_token_novo_e_revogar_um_nao_afeta_o_outro(session, fake_sender, cfg):
    a = _logar(session, fake_sender, cfg)
    b = _logar(session, fake_sender, cfg)
    assert a.token != b.token
    assert revoke_session(session, a.token)
    assert authenticate_session(session, a.token) is None
    assert authenticate_session(session, b.token) is not None


# ============================================================================ logs
def test_logs_nao_contem_codigo_nem_email_completo(session, fake_sender, cfg, caplog):
    with caplog.at_level(logging.DEBUG, logger="minhoca"):
        _pedir(session, fake_sender, cfg)
        codigo = fake_sender.last_code(EMAIL)
        verify_login_code(session, email=EMAIL, code=codigo, cfg=cfg)
        fake_sender.fail_with = EmailSendError("falha")
        shift_login_codes(session, 61)
        with pytest.raises(EmailUnavailableError):
            _pedir(session, fake_sender, cfg)
    assert caplog.text  # houve log
    assert EMAIL not in caplog.text and "ana@" not in caplog.text
    assert codigo not in caplog.text
    assert IP not in caplog.text  # nenhum IP bruto
