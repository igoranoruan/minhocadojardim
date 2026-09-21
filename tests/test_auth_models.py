"""Constraints de login_codes e auth_sessions, incluindo a invariável "um único código aberto por e-mail"."""
import hashlib
from datetime import timedelta

import pytest
from sqlalchemy import select, text, update

from database.models import AuthSession, LoginCode
from database.types import utcnow


def _codigo(email="ana@example.com", *, fechado=False, **overrides) -> LoginCode:
    agora = utcnow()
    dados = dict(
        email=email,
        code_hash="a" * 64,
        attempts=0,
        created_at=agora,
        expires_at=agora + timedelta(minutes=10),
        active_slot=None if fechado else 1,
        closed_at=agora if fechado else None,
        close_reason="superseded" if fechado else None,
    )
    dados.update(overrides)
    return LoginCode(**dados)


@pytest.fixture()
def add(session):
    def _add(obj):
        session.add(obj)
        session.commit()
        return obj

    return _add


# ------------------------------------------------------------------ invariável: um único código aberto por e-mail
def test_nao_existem_dois_codigos_abertos_para_o_mesmo_email(add, assert_rejected):
    add(_codigo())
    assert_rejected(lambda: add(_codigo()))


def test_o_email_e_comparado_ja_normalizado(add, assert_rejected):
    add(_codigo("ana@example.com"))
    assert_rejected(lambda: add(_codigo("ANA@example.com")))  # o CHECK de e-mail recusa o valor fora do padrão


def test_varios_codigos_fechados_do_mesmo_email_sao_permitidos(add, session):
    for _ in range(4):
        add(_codigo(fechado=True))
    add(_codigo())  # e ainda cabe um aberto
    total = session.execute(text("SELECT COUNT(*) FROM login_codes WHERE email = 'ana@example.com'")).scalar_one()
    assert total == 5


def test_emails_diferentes_podem_ter_cada_um_um_codigo_aberto(add):
    add(_codigo("ana@example.com"))
    add(_codigo("bia@example.com"))


def test_fechar_o_codigo_libera_a_abertura_de_outro(add, session):
    primeiro = add(_codigo())
    session.execute(
        update(LoginCode)
        .where(LoginCode.id == primeiro.id)
        .values(closed_at=utcnow(), close_reason="superseded", active_slot=None)
    )
    session.commit()
    add(_codigo())  # agora pode


def test_reabrir_um_codigo_fechado_nao_e_possivel_se_ja_ha_outro_aberto(add, session, assert_rejected):
    fechado = add(_codigo(fechado=True))
    add(_codigo())

    def reabrir():
        session.execute(
            update(LoginCode)
            .where(LoginCode.id == fechado.id)
            .values(active_slot=1, closed_at=None, close_reason=None)
        )
        session.commit()

    assert_rejected(reabrir)


# ------------------------------------------------------------------ CHECKs de login_codes
def test_codigo_aberto_ou_fechado_de_forma_consistente(add, assert_rejected):
    agora = utcnow()
    assert_rejected(lambda: add(_codigo(active_slot=1, closed_at=agora, close_reason="verified")))
    assert_rejected(lambda: add(_codigo(active_slot=None, closed_at=None, close_reason=None)))
    assert_rejected(lambda: add(_codigo(active_slot=None, closed_at=agora, close_reason=None)))
    assert_rejected(lambda: add(_codigo(active_slot=1, closed_at=None, close_reason="verified")))


# Matriz completa: active_slot (1 ou NULL) x closed_at (NULL ou preenchido) x close_reason (NULL ou preenchido).
# Só DOIS estados são válidos: aberto (1, NULL, NULL) e fechado (NULL, preenchido, preenchido).
_ESTADOS = [
    # (active_slot, tem closed_at, tem close_reason, é válido)
    (1, False, False, True),  # ABERTO
    (None, True, True, True),  # FECHADO
    (None, False, False, False),  # sem active_slot e sem fechamento: era o buraco (resultado NULL no CHECK)
    (None, True, False, False),  # fechado sem motivo
    (None, False, True, False),  # motivo sem closed_at
    (1, True, True, False),  # aberto e fechado ao mesmo tempo
    (1, True, False, False),  # aberto com closed_at
    (1, False, True, False),  # aberto com motivo
]


@pytest.mark.parametrize(
    "active_slot, tem_closed_at, tem_motivo, valido",
    _ESTADOS,
    ids=["aberto", "fechado", "nada-preenchido", "fechado-sem-motivo", "motivo-sem-closed_at",
         "1-com-tudo", "1-com-closed_at", "1-com-motivo"],
)
def test_matriz_de_estados_aberto_e_fechado(add, assert_rejected, active_slot, tem_closed_at, tem_motivo, valido):
    agora = utcnow()
    criar = lambda: add(  # noqa: E731
        _codigo(
            active_slot=active_slot,
            closed_at=agora if tem_closed_at else None,
            close_reason="verified" if tem_motivo else None,
        )
    )
    if valido:
        criar()
    else:
        assert_rejected(criar)


def test_atualizacao_nao_pode_deixar_o_codigo_num_estado_invalido(add, session, assert_rejected):
    aberto = add(_codigo())

    def atualizar(**valores):
        def _run():
            session.execute(update(LoginCode).where(LoginCode.id == aberto.id).values(**valores))
            session.commit()

        return _run

    # "soltar" o active_slot sem registrar o fechamento deixaria o código sem estado
    assert_rejected(atualizar(active_slot=None))
    # fechar pela metade
    assert_rejected(atualizar(active_slot=None, closed_at=utcnow()))
    assert_rejected(atualizar(active_slot=None, close_reason="verified"))
    # fechar por inteiro é o caminho correto
    atualizar(active_slot=None, closed_at=utcnow(), close_reason="verified")()


def test_active_slot_so_aceita_1_ou_nulo(add, assert_rejected):
    assert_rejected(lambda: add(_codigo(active_slot=2)))
    assert_rejected(lambda: add(_codigo(active_slot=0)))


def test_motivo_de_fechamento_valido(add, assert_rejected):
    for motivo in ("verified", "superseded", "exhausted", "send_failed"):
        add(_codigo(fechado=True, close_reason=motivo))
    assert_rejected(lambda: add(_codigo(fechado=True, close_reason="expirou")))


def test_regras_de_integridade_do_codigo(add, assert_rejected):
    agora = utcnow()
    assert_rejected(lambda: add(_codigo(attempts=-1)))
    assert_rejected(lambda: add(_codigo(code_hash="a" * 63)))
    assert_rejected(lambda: add(_codigo(code_hash="a" * 65)))
    assert_rejected(lambda: add(_codigo(created_at=agora, expires_at=agora)))
    assert_rejected(lambda: add(_codigo(created_at=agora, expires_at=agora - timedelta(seconds=1))))
    assert_rejected(lambda: add(_codigo("Ana@Example.com")))  # e-mail fora do padrão strip().lower()
    assert_rejected(lambda: add(_codigo(" ana@example.com")))


def test_login_codes_nao_tem_fk_para_users(session):
    # o usuário só é criado depois do código correto: o e-mail do código não precisa existir em users
    session.add(_codigo("nunca-visto@example.com"))
    session.commit()
    assert session.execute(text("SELECT COUNT(*) FROM users")).scalar_one() == 0


# ------------------------------------------------------------------ auth_sessions
def _sessao(user_id, **overrides) -> AuthSession:
    agora = utcnow()
    dados = dict(
        user_id=user_id,
        token_hash=hashlib.sha256(b"token").hexdigest(),
        created_at=agora,
        last_used_at=agora,
        expires_at=agora + timedelta(days=30),
    )
    dados.update(overrides)
    return AuthSession(**dados)


def test_sessao_valida_e_token_hash_unico(add, factory, assert_rejected):
    usuario = factory.user()
    add(_sessao(usuario.id))
    assert_rejected(lambda: add(_sessao(usuario.id)))  # mesmo token_hash


def test_regras_de_integridade_da_sessao(add, factory, assert_rejected):
    usuario = factory.user()
    agora = utcnow()
    assert_rejected(lambda: add(_sessao(usuario.id, token_hash="b" * 63)))
    assert_rejected(lambda: add(_sessao(usuario.id, token_hash="c" * 64, created_at=agora, expires_at=agora)))
    assert_rejected(
        lambda: add(_sessao(usuario.id, token_hash="d" * 64, created_at=agora, expires_at=agora - timedelta(days=1)))
    )


def test_sessao_exige_usuario_existente_e_nao_permite_apagar_usuario(add, factory, session, assert_rejected):
    assert_rejected(lambda: add(_sessao(999_999)))  # FK ativa
    usuario = factory.user()
    add(_sessao(usuario.id))
    session.delete(usuario)
    assert_rejected(session.commit)  # RESTRICT


def test_revoked_at_e_last_used_at(add, factory, session):
    usuario = factory.user()
    obj = add(_sessao(usuario.id))
    assert obj.revoked_at is None
    obj.revoked_at = utcnow()
    session.commit()
    assert session.execute(select(AuthSession.revoked_at)).scalar_one() is not None
