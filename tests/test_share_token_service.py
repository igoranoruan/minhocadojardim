"""Token pessoal do atalho "Enviar para o KLANGO" (15/10/2026): geração, hash salvo (nunca o
token puro), revogação, invariável "1 token ativo por usuário", e autenticação (válido/inválido/
revogado)."""
from sqlalchemy import select

from database.models import ShareToken
from services.share_queue import (
    authenticate_share_token,
    extract_token_from_request,
    generate_token,
    get_token_status,
    revoke_token,
)
from utils.security import hash_share_token


def test_generate_token_devolve_valor_e_grava_so_o_hash(factory, session):
    usuario = factory.user()
    token = generate_token(session, usuario)

    assert isinstance(token, str) and len(token) > 20
    linha = session.execute(select(ShareToken).where(ShareToken.user_id == usuario.id)).scalar_one()
    assert linha.token_hash == hash_share_token(token)
    assert linha.token_hash != token  # nunca o valor puro gravado


def test_generate_novo_token_revoga_o_anterior_automaticamente(factory, session):
    usuario = factory.user()
    primeiro = generate_token(session, usuario)
    segundo = generate_token(session, usuario)

    assert primeiro != segundo
    ativos = session.execute(
        select(ShareToken).where(ShareToken.user_id == usuario.id, ShareToken.revoked_at.is_(None))
    ).scalars().all()
    assert len(ativos) == 1
    assert ativos[0].token_hash == hash_share_token(segundo)

    # o token antigo não autentica mais
    assert authenticate_share_token(session, primeiro) is None
    assert authenticate_share_token(session, segundo) is not None


def test_get_token_status_none_quando_nunca_gerou(factory, session):
    usuario = factory.user()
    assert get_token_status(session, usuario) is None


def test_get_token_status_depois_de_gerar(factory, session):
    usuario = factory.user()
    generate_token(session, usuario)
    status = get_token_status(session, usuario)
    assert status is not None
    assert status.last_used_at is None


def test_revoke_token_impede_autenticacao_e_e_idempotente(factory, session):
    usuario = factory.user()
    token = generate_token(session, usuario)

    assert revoke_token(session, usuario) is True
    assert authenticate_share_token(session, token) is None
    assert get_token_status(session, usuario) is None
    assert revoke_token(session, usuario) is False  # já não havia token ativo


def test_authenticate_com_token_invalido_ou_vazio_devolve_none(session):
    assert authenticate_share_token(session, None) is None
    assert authenticate_share_token(session, "") is None
    assert authenticate_share_token(session, "x" * 600) is None  # tamanho absurdo
    assert authenticate_share_token(session, "token-que-nunca-foi-gerado") is None


def test_authenticate_com_token_valido_devolve_o_dono_e_atualiza_last_used_at(factory, session):
    usuario = factory.user()
    token = generate_token(session, usuario)

    autenticado = authenticate_share_token(session, token)
    assert autenticado is not None
    assert autenticado.id == usuario.id

    status = get_token_status(session, usuario)
    assert status.last_used_at is not None


def test_token_de_um_usuario_nao_autentica_outro(factory, session):
    usuario_a = factory.user()
    usuario_b = factory.user()
    token_a = generate_token(session, usuario_a)

    autenticado = authenticate_share_token(session, token_a)
    assert autenticado.id == usuario_a.id
    assert autenticado.id != usuario_b.id


# ------------------------------------------------------------------ extract_token_from_request
def test_extract_token_prioriza_o_header_bearer():
    token = extract_token_from_request(authorization_header="Bearer abc123", body_token="outro")
    assert token == "abc123"


def test_extract_token_usa_o_corpo_quando_nao_ha_header():
    token = extract_token_from_request(authorization_header=None, body_token="do-corpo")
    assert token == "do-corpo"


def test_extract_token_ignora_header_sem_esquema_bearer():
    token = extract_token_from_request(authorization_header="Basic abc123", body_token="do-corpo")
    assert token == "do-corpo"


def test_extract_token_devolve_none_sem_header_nem_corpo():
    assert extract_token_from_request(authorization_header=None, body_token=None) is None
    assert extract_token_from_request(authorization_header="", body_token="   ") is None
