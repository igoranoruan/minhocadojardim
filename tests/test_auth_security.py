import base64
import hashlib

import pytest

from utils import security
from utils.security import (
    constant_time_equals,
    generate_login_code,
    generate_session_token,
    hash_ip,
    hash_login_code,
    hash_session_token,
    is_valid_code_format,
    is_valid_email_format,
    mask_email,
)


def test_codigo_tem_6_digitos_e_e_sempre_string():
    for _ in range(300):
        codigo = generate_login_code()
        assert isinstance(codigo, str)
        assert len(codigo) == 6 and codigo.isdigit()


def test_codigo_preserva_zeros_a_esquerda(monkeypatch):
    monkeypatch.setattr(security.secrets, "randbelow", lambda limite: 7)
    assert generate_login_code() == "000007"


def test_formato_do_codigo():
    assert is_valid_code_format("000123")
    for invalido in ("12345", "1234567", "12345a", " 12345", "١٢٣٤٥٦", "", None, 123456):
        assert not is_valid_code_format(invalido)


def test_hash_do_codigo_nao_e_o_codigo_nem_um_sha256_simples():
    email, codigo, segredo = "ana@example.com", "123456", "segredo-de-teste-com-mais-de-32-caracteres"
    digest = hash_login_code(email, codigo, segredo)
    assert digest != codigo
    assert len(digest) == 64
    assert digest != hashlib.sha256(codigo.encode()).hexdigest()  # depende do segredo do servidor
    assert digest == hash_login_code(email, codigo, segredo)  # determinístico
    assert digest != hash_login_code("outra@example.com", codigo, segredo)  # amarrado ao e-mail
    assert digest != hash_login_code(email, codigo, segredo + "x")  # muda com o segredo


def test_hash_do_ip_usa_segredo_proprio_e_nao_expoe_o_ip():
    digest = hash_ip("203.0.113.9", "segredo-de-ip-com-mais-de-32-caracteres!!")
    assert len(digest) == 64
    assert "203.0.113.9" not in digest
    assert digest != hash_ip("203.0.113.9", "outro-segredo-de-ip-com-32-caracteres!!")


def test_token_de_sessao_tem_256_bits_e_o_banco_guarda_so_o_sha256():
    tokens = {generate_session_token() for _ in range(50)}
    assert len(tokens) == 50  # não repete
    token = next(iter(tokens))
    bruto = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    assert len(bruto) == 32  # 256 bits
    assert hash_session_token(token) == hashlib.sha256(token.encode()).hexdigest()
    assert hash_session_token(token) != token


def test_validacao_de_formato_de_email():
    for valido in ("ana@example.com", "ana.silva+tag@sub.example.com.br"):
        assert is_valid_email_format(valido)
    for invalido in ("", "ana", "ana@example", "a b@example.com", "a@@example.com", "@example.com",
                     "ana@example.com\nBcc: alguem@example.com", "ana@exam\x00ple.com", None):
        assert not is_valid_email_format(invalido)
    assert not is_valid_email_format("a" * 320 + "@example.com")


def test_mask_email_nao_expoe_o_endereco_completo():
    mascarado = mask_email("ana@exemplo.com")
    assert mascarado == "a***@e***"
    assert "ana@exemplo.com" not in mascarado


def test_comparacao_em_tempo_constante():
    assert constant_time_equals("abc", "abc")
    assert not constant_time_equals("abc", "abd")
    assert not constant_time_equals("abc", "abcd")


@pytest.mark.parametrize("nome", ["generate_login_code", "hash_login_code", "hash_ip"])
def test_funcoes_exportadas(nome):
    assert callable(getattr(security, nome))
