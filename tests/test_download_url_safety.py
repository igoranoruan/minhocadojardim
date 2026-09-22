"""URL safety / SSRF: esquema, IP literal, DNS privado/loopback/link-local/reservado, e
redirecionamento resolvido manualmente (cada salto revalidado). Tudo offline (servidor local).
"""
import socket

import ipaddress

import pytest

from download.errors import InvalidUrlError, SsrfBlockedError, TooManyRedirectsError
from download.url_safety import resolve_redirect_chain, resolve_host_ips, validate_url
from helpers_download import LocalHttpServer


def _allow_only_local_test_server(monkeypatch):
    """Trata só 127.0.0.1 (o servidor de teste local) como "público"; tudo o mais é avaliado
    pela regra REAL de bloqueio. Nunca desliga a proteção inteira — isso testaria em falso
    positivo, deixando passar um cenário de SSRF real sem que o teste percebesse.
    """
    def bloqueado(ip_str):
        if ip_str == "127.0.0.1":
            return False
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return True
        return (
            ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified
        )

    monkeypatch.setattr("download.url_safety._is_blocked_ip", bloqueado)


# ------------------------------------------------------------------ esquema e formato
@pytest.mark.parametrize("url", ["", "   ", None, 123])
def test_url_vazia_ou_invalida(url):
    with pytest.raises(InvalidUrlError):
        validate_url(url)


@pytest.mark.parametrize("esquema", ["ftp", "file", "gopher", "data", "javascript"])
def test_esquema_nao_permitido(esquema):
    with pytest.raises(InvalidUrlError):
        validate_url(f"{esquema}://example.com/x")


def test_url_sem_host():
    with pytest.raises(InvalidUrlError):
        validate_url("https:///path")


# ------------------------------------------------------------------ IP literal
@pytest.mark.parametrize(
    "ip", ["127.0.0.1", "10.0.0.5", "192.168.1.1", "172.16.0.1", "169.254.169.254", "0.0.0.0", "::1"]
)
def test_ip_literal_privado_ou_reservado_e_bloqueado(ip):
    host = f"[{ip}]" if ":" in ip else ip
    with pytest.raises(SsrfBlockedError):
        validate_url(f"http://{host}/x")


def test_ip_literal_publico_passa_na_validacao():
    validado = validate_url("http://93.184.216.34/x")  # IP público de exemplo (documentação IANA)
    assert validado.resolved_ips == ("93.184.216.34",)


# ------------------------------------------------------------------ DNS: privado/loopback/link-local/reservado
def test_dns_que_resolve_para_ip_privado_e_bloqueado(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, port: [(2, 1, 6, "", ("10.1.2.3", 0))])
    with pytest.raises(SsrfBlockedError):
        validate_url("http://interno.exemplo.test/x")


def test_dns_que_resolve_para_loopback_e_bloqueado(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, port: [(2, 1, 6, "", ("127.0.0.1", 0))])
    with pytest.raises(SsrfBlockedError):
        validate_url("http://loopback.exemplo.test/x")


def test_dns_que_resolve_para_link_local_e_bloqueado(monkeypatch):
    # 169.254.169.254: metadados de nuvem (AWS/GCP/Azure) — caso clássico de SSRF.
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, port: [(2, 1, 6, "", ("169.254.169.254", 0))])
    with pytest.raises(SsrfBlockedError):
        validate_url("http://metadata.exemplo.test/x")


def test_dns_com_um_registro_privado_entre_varios_bloqueia_o_host_inteiro(monkeypatch):
    # Nunca "valida um registro e conecta em outro": um único IP interno já derruba o host.
    enderecos = [(2, 1, 6, "", ("93.184.216.34", 0)), (2, 1, 6, "", ("10.0.0.1", 0))]
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, port: enderecos)
    with pytest.raises(SsrfBlockedError):
        validate_url("http://misto.exemplo.test/x")


def test_dns_que_nao_resolve_e_erro_de_url_invalida(monkeypatch):
    def falha(host, port):
        raise socket.gaierror("não resolvido")

    monkeypatch.setattr(socket, "getaddrinfo", falha)
    with pytest.raises(InvalidUrlError):
        validate_url("http://nao-existe.exemplo.test/x")


def test_dns_publico_passa(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, port: [(2, 1, 6, "", ("93.184.216.34", 0))])
    validado = validate_url("https://tiktok.com/@a/video/1")
    assert validado.resolved_ips == ("93.184.216.34",)
    assert validado.scheme == "https" and validado.host == "tiktok.com" and validado.port == 443


def test_resolve_host_ips_devolve_todos_os_enderecos(monkeypatch):
    enderecos = [(2, 1, 6, "", ("93.184.216.34", 0)), (2, 1, 6, "", ("93.184.216.35", 0))]
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, port: enderecos)
    assert resolve_host_ips("exemplo.test") == ("93.184.216.34", "93.184.216.35")


# ------------------------------------------------------------------ redirecionamento (servidor local real)
def test_redirect_para_destino_publico_e_seguido(monkeypatch):
    with LocalHttpServer({"/inicio": (302, {}), "/fim": (200, {})}) as server:
        _allow_only_local_test_server(monkeypatch)
        server._httpd.RequestHandlerClass.routes["/inicio"] = (302, {"Location": server.url("/fim")})
        resultado = resolve_redirect_chain(server.url("/inicio"))
        assert resultado.url == server.url("/fim")


def test_redirect_para_ip_privado_e_bloqueado(monkeypatch):
    with LocalHttpServer({"/inicio": (302, {"Location": "http://169.254.169.254/secreto"})}) as server:
        _allow_only_local_test_server(monkeypatch)
        with pytest.raises(SsrfBlockedError):
            resolve_redirect_chain(server.url("/inicio"))


def test_redirect_ciclico_e_detectado(monkeypatch):
    with LocalHttpServer({}) as server:
        _allow_only_local_test_server(monkeypatch)
        a, b = server.url("/a"), server.url("/b")
        server._httpd.RequestHandlerClass.routes["/a"] = (302, {"Location": b})
        server._httpd.RequestHandlerClass.routes["/b"] = (302, {"Location": a})
        with pytest.raises(TooManyRedirectsError):
            resolve_redirect_chain(a)


def test_cadeia_longa_demais_e_recusada(monkeypatch):
    with LocalHttpServer({}) as server:
        _allow_only_local_test_server(monkeypatch)
        for i in range(8):
            server._httpd.RequestHandlerClass.routes[f"/{i}"] = (302, {"Location": server.url(f"/{i + 1}")})
        server._httpd.RequestHandlerClass.routes["/8"] = (200, {})
        with pytest.raises(TooManyRedirectsError):
            resolve_redirect_chain(server.url("/0"), max_redirects=5)


def test_sem_redirect_devolve_a_propria_url(monkeypatch):
    with LocalHttpServer({"/ok": (200, {})}) as server:
        _allow_only_local_test_server(monkeypatch)
        resultado = resolve_redirect_chain(server.url("/ok"))
        assert resultado.url == server.url("/ok")


def test_redirect_para_ip_privado_e_bloqueado_sem_nenhum_bypass(monkeypatch):
    """Igual ao teste acima, mas SEM helper nenhum: prova que _is_blocked_ip real (não mockado)
    bloqueia um redirecionamento malicioso de ponta a ponta."""
    with LocalHttpServer({"/mal": (302, {"Location": "http://10.0.0.1/interno"})}) as server:
        with pytest.raises(SsrfBlockedError):
            resolve_redirect_chain(server.url("/mal"))


def test_falha_no_head_nao_e_tratada_como_ssrf(monkeypatch):
    # Porta fechada no próprio servidor local: erro de conexão (não de SSRF). A checagem
    # antecipada não é garantia de disponibilidade — quem decide se o download prossegue é o
    # downloader (documentado no módulo). Usamos "127.0.0.1 tratado como público" só para isolar
    # o comportamento de falha de conexão do bloqueio por IP privado, que é testado à parte acima.
    _allow_only_local_test_server(monkeypatch)
    url = "http://127.0.0.1:1/inexistente"  # porta 1: reservada, ninguém escuta nela
    resultado = resolve_redirect_chain(url)
    assert resultado.url == url
