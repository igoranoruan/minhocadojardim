"""Validação de URL e proteção contra SSRF.

Cobre: esquema permitido, IP literal, DNS para IP privado/loopback/link-local/reservado, e
resolução MANUAL de redirecionamento (sem seguir automaticamente), revalidando cada salto — só
para as requisições que a NOSSA camada faz diretamente (ver `resolve_redirect_chain`), como uma
checagem antecipada antes de acionar o yt-dlp.

LIMITAÇÃO DOCUMENTADA (não inventamos proteção que não existe):
1. DNS rebinding (TOCTOU): resolvemos o host e validamos os IPs, mas a conexão real (seja do
   nosso urllib de pré-checagem, seja do yt-dlp) resolve o DNS de novo ao conectar. Um host que
   responde IP público na nossa checagem e IP privado no connect real passaria. Fechar essa
   brecha por completo exigiria fixar (pin) o IP validado na conexão TCP (bypassando o resolver
   do socket), o que nem o urllib nem o yt-dlp expõem como opção pronta. Fica registrado como
   risco residual aceito nesta etapa — mitigação real é assunto de endurecimento futuro (um
   wrapper de socket que resolve uma vez e fixa o IP).
2. yt-dlp resolve REDIRECIONAMENTOS E REQUISIÇÕES PRÓPRIAS internamente (a página da plataforma,
   a URL final do arquivo de mídia/CDN) — essas conexões NÃO passam pelo `resolve_redirect_chain`
   daqui, porque são feitas pelo cliente HTTP interno do yt-dlp, não pelo nosso urllib. O yt-dlp
   não oferece um hook de validação de destino por requisição (confirmado: não existe opção
   pública equivalente a um "url validator" chamado a cada requisição HTTP interna). A mitigação
   dentro do escopo é a combinação: (a) `allowed_extractors` restrito ao extractor exato da
   plataforma detectada — NUNCA o extractor "generic" (que tem um histórico de abuso documentado:
   GHSA-3ch3-jhc6-5r8x, URL smuggling de proxy arbitrário via o extractor genérico); (b) a lista
   fechada de domínios de platform.py, que garante que o yt-dlp só é chamado para o site da
   própria plataforma, nunca para uma URL arbitrária; (c) manter a validação de certificado TLS
   ligada (nunca passar `nocheckcertificate=True`); (d) nunca aceitar proxy vindo de entrada do
   usuário. Isso reduz a superfície ao que o próprio código oficial de cada extractor faz (CDNs
   legítimos da plataforma), mas não é uma garantia formal de que nenhum salto interno do yt-dlp
   toque um IP privado — por isso NÃO afirmamos "SSRF resolvido", e sim "mitigado dentro do que a
   biblioteca expõe".

Assim, `resolve_redirect_chain` é defesa em profundidade sobre a URL de ENTRADA (a que o usuário
colou), não uma garantia sobre tudo que o yt-dlp faz depois.
"""
import ipaddress
import socket
from dataclasses import dataclass
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from config import MAX_REDIRECTS
from download.errors import InvalidUrlError, SsrfBlockedError, TooManyRedirectsError

ALLOWED_SCHEMES = frozenset({"http", "https"})
_DEFAULT_PORTS = {"http": 80, "https": 443}


@dataclass(frozen=True)
class ValidatedUrl:
    url: str
    scheme: str
    host: str
    port: int
    resolved_ips: tuple[str, ...]


def _is_blocked_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # não parseou: trata como bloqueado (falha fechada)
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def resolve_host_ips(host: str) -> tuple[str, ...]:
    """Resolve A/AAAA do host. Levanta InvalidUrlError se não resolver nenhum."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise InvalidUrlError(f"não foi possível resolver o host: {host!r}") from None
    ips = tuple(sorted({info[4][0] for info in infos}))
    if not ips:
        raise InvalidUrlError(f"host sem endereço IP: {host!r}")
    return ips


def validate_url(url: str) -> ValidatedUrl:
    """Valida esquema, host e (via DNS) que NENHUM ip resolvido é privado/loopback/link-local/
    reservado/multicast. Um único IP interno no conjunto já bloqueia o host inteiro — nunca
    "valida um registro e conecta em outro" (ver limitação de DNS rebinding no docstring do
    módulo: a validação vale para ESTE instante de resolução).
    """
    if not isinstance(url, str) or not url.strip():
        raise InvalidUrlError("URL vazia.")
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise InvalidUrlError(f"esquema não permitido: {scheme!r}")
    host = parts.hostname
    if not host:
        raise InvalidUrlError("URL sem host.")
    host = host.lower()
    port = parts.port or _DEFAULT_PORTS[scheme]

    # IP literal na própria URL (ex.: http://169.254.169.254/): valida direto, sem DNS.
    try:
        ipaddress.ip_address(host)
        literal_ip = True
    except ValueError:
        literal_ip = False

    ips = (host,) if literal_ip else resolve_host_ips(host)
    if any(_is_blocked_ip(ip) for ip in ips):
        raise SsrfBlockedError(f"host resolve para endereço bloqueado: {host!r} -> {ips!r}")

    return ValidatedUrl(url=url, scheme=scheme, host=host, port=port, resolved_ips=ips)


class _NoAutoRedirect(HTTPRedirectHandler):
    """redirect_request() devolvendo None é a forma documentada de dizer ao urllib 'não siga
    este redirecionamento sozinho'. SEM isso, urlopen() segue 3xx automaticamente ANTES do nosso
    código sequer ver o cabeçalho Location — ou seja, sem este handler, a validação de cada salto
    abaixo nunca chegaria a rodar (a conexão real já teria acontecido). Isso é um comportamento
    documentado do urllib (o mesmo bug de classe já reportado publicamente em outras bibliotecas,
    ex. GHSA-983w-rhvv-gwmv), não uma suposição nossa.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


_OPENER = build_opener(_NoAutoRedirect)


def resolve_redirect_chain(url: str, *, max_redirects: int = MAX_REDIRECTS) -> ValidatedUrl:
    """Segue redirecionamentos MANUALMENTE (nunca automático — ver _NoAutoRedirect acima),
    validando CADA salto com validate_url ANTES de segui-lo. Usa só HEAD (nenhum corpo é baixado
    aqui). É a checagem que a NOSSA camada controla — ver limitação (2) no docstring do módulo
    sobre o que isso NÃO cobre (requisições que o próprio yt-dlp faz depois).
    """
    current = validate_url(url)
    seen = {current.url}
    for _ in range(max_redirects):
        request = Request(current.url, method="HEAD", headers={"User-Agent": "MinhocaDeJardim/1.0"})
        try:
            with _OPENER.open(request, timeout=10) as response:  # noqa: S310 - URL já validada acima
                status, headers = response.status, response.headers
        except HTTPError as exc:  # 3xx (e qualquer 4xx/5xx) chegam aqui como exceção, com headers
            status, headers = exc.code, exc.headers
        except Exception:
            # Falha de conexão (não é resposta HTTP nenhuma) não é, por si só, SSRF: quem decide
            # se o download prossegue é o downloader da plataforma, com a própria URL já validada.
            return current
        location = headers.get("Location") if headers else None
        if status not in (301, 302, 303, 307, 308) or not location:
            return current
        next_validated = validate_url(location)
        if next_validated.url in seen:
            raise TooManyRedirectsError("redirecionamento cíclico detectado.")
        seen.add(next_validated.url)
        current = next_validated
    raise TooManyRedirectsError(f"mais de {max_redirects} redirecionamentos.")
