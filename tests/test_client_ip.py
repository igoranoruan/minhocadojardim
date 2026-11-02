"""routes/deps.py::client_ip -- IP REAL do cliente atrás do proxy do Render (pedido do Igor,
02/11/2026). Antes, `request.client.host` era o nó do balanceador interno (10.x), igual para todo
mundo: as travas por IP (teste VIP, código de login) valiam para o site inteiro."""
import pytest
from starlette.requests import Request

from routes.deps import client_ip, client_ip_with_source, mask_ip


def _req(peer: str | None, headers: dict[str, str] | None = None) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        "client": (peer, 12345) if peer is not None else None,
    }
    return Request(scope)


# ============================================================================ atrás do proxy do Render (peer 10.x)
def test_prefere_cf_connecting_ip():
    req = _req("10.24.160.187", {
        "CF-Connecting-IP": "187.12.34.56",
        "True-Client-IP": "200.1.1.1",
        "X-Forwarded-For": "1.2.3.4, 10.24.160.187",
    })
    assert client_ip_with_source(req) == ("187.12.34.56", "cf-connecting-ip")


def test_sem_cloudflare_usa_true_client_ip():
    req = _req("10.24.160.187", {"True-Client-IP": "200.1.1.1", "X-Forwarded-For": "1.2.3.4"})
    assert client_ip_with_source(req) == ("200.1.1.1", "true-client-ip")


def test_so_x_forwarded_for_usa_a_primeira_entrada():
    req = _req("10.28.26.132", {"X-Forwarded-For": "177.10.20.30, 172.70.1.1, 10.28.26.132"})
    assert client_ip_with_source(req) == ("177.10.20.30", "x-forwarded-for")


def test_cabecalho_com_valor_invalido_e_ignorado():
    req = _req("10.28.26.132", {"CF-Connecting-IP": "lixo", "X-Forwarded-For": "177.10.20.30"})
    assert client_ip_with_source(req) == ("177.10.20.30", "x-forwarded-for")


def test_sem_nenhum_cabecalho_cai_no_proprio_peer():
    assert client_ip_with_source(_req("10.31.27.51")) == ("10.31.27.51", "peer")


def test_ipv6_valido_e_aceito():
    req = _req("10.0.0.1", {"CF-Connecting-IP": "2804:14c:1234:5678::1"})
    assert client_ip(req) == "2804:14c:1234:5678::1"


# ============================================================================ falsificação
def test_conexao_direta_de_ip_publico_ignora_cabecalhos():
    """Quem NÃO passa pelo proxy (peer público) não escolhe o próprio IP mandando cabeçalho."""
    req = _req("203.0.113.9", {"CF-Connecting-IP": "1.1.1.1", "X-Forwarded-For": "1.1.1.1"})
    assert client_ip_with_source(req) == ("203.0.113.9", "peer")


def test_peer_que_nem_e_ip_ignora_cabecalhos():
    req = _req("testclient", {"CF-Connecting-IP": "1.1.1.1"})
    assert client_ip_with_source(req) == ("testclient", "peer")


def test_sem_client_devolve_none():
    assert client_ip_with_source(_req(None)) == (None, "none")


# ============================================================================ log mascarado
@pytest.mark.parametrize(
    ("ip", "esperado"),
    [
        ("187.12.34.56", "187.12.x.x"),
        ("2804:14c:1234:5678::1", "2804:014c:x"),
        (None, "-"),
        ("testclient", "?"),
    ],
)
def test_mask_ip_nunca_mostra_o_ip_inteiro(ip, esperado):
    assert mask_ip(ip) == esperado
