"""Implementação compartilhada de PlatformDownloader baseada em yt-dlp.

As quatro plataformas (TikTok, Instagram, Pinterest, YouTube) usam esta MESMA classe,
parametrizada por um YtDlpSpec — evita 4 arquivos quase idênticos. A seleção por plataforma
continua explícita: quem decide qual spec usar é o registro em download/service.py, não herança.

yt-dlp é a ÚNICA dependência externa desta camada, e É IMPORTADO SÓ AQUI: nenhum outro módulo do
projeto (fora de download/) conhece yt-dlp — o contrato público é download.service.download_video,
que devolve só DownloadResult (download/result.py), sem nenhum tipo do yt-dlp vazando para fora.

Opções de segurança usadas (ver a análise completa em download/url_safety.py, no topo do módulo):
- allowed_extractors: restringe ao(s) extractor(es) EXATOS da plataforma. NUNCA inclui "generic"
  nem lista aberta — é a opção oficial do yt-dlp para isso (substituiu --force-generic-extractor;
  aceita nomes/regex de extractor, com "-generic" para excluir explicitamente). Sem essa
  restrição, uma URL que o yt-dlp não reconhecesse cairia no extractor genérico, que tem um
  histórico de abuso documentado (GHSA-3ch3-jhc6-5r8x: URL smuggling permitindo proxy arbitrário).
- max_filesize: o yt-dlp aborta o PRÓPRIO streaming ao ultrapassar o limite — é assim que os
  100 MB são aplicados sem que este código precise ler o corpo da resposta (e sem carregar nada
  em RAM: quem lê os bytes é o downloader interno do yt-dlp, em pedaços, gravando direto em disco).
- nocheckcertificate=False (explícito): nunca desligamos a validação de certificado TLS.
- cookiefile=None: nunca há um arquivo de cookies pessoais como padrão neste projeto (regra do
  produto: nenhuma sessão logada do usuário é usada para autenticar o download).
- restrictfilenames/windowsfilenames/trim_file_name: nomes de arquivo internos do yt-dlp também
  saneados — mas o nome que realmente usamos (dest_stub) já vem de download/tempfiles.py, gerado
  por nós, nunca do título ou de metadados da plataforma (mitiga a classe de falha do
  CVE-2024-38519, sanitização insuficiente de extensão/nome; reforçado por
  download/file_validation.py, que só aceita uma extensão de uma lista fechada).

yt-dlp>=2026.6.9 em requirements.txt: versão posterior às correções acima e à falha de escrita de
arquivo arbitrária via aria2c como downloader externo (por isso este projeto NUNCA configura um
downloader externo — usa sempre o downloader nativo do yt-dlp).
"""
import logging
from dataclasses import dataclass, field
from pathlib import Path

import yt_dlp
from yt_dlp.networking.impersonate import ImpersonateTarget

from config import DOWNLOAD_CONNECT_TIMEOUT_SECONDS, MAX_VIDEO_SIZE_BYTES, get_settings
from download.base import PlatformDownloader, RawDownload
from download.errors import DownloadFailedError, DownloadTimeoutError, VideoTooLargeError
from download.platform import Platform

logger = logging.getLogger("minhoca")

# Motivos técnicos (log) categorizados a partir da mensagem do yt-dlp — a mensagem crua NUNCA
# chega ao usuário (DownloadError.user_message já é genérica) nem é logada por inteiro aqui,
# porque pode conter a URL ou detalhes internos da plataforma.
_REASON_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("login", "conteúdo exige login na plataforma de origem"),
    ("sign in", "conteúdo exige login na plataforma de origem"),
    ("private", "conteúdo indisponível ou privado"),
    ("unavailable", "conteúdo indisponível"),
    ("removed", "conteúdo removido"),
    ("unsupported url", "URL não reconhecida pelo extractor"),
    ("timed out", "tempo de rede esgotado"),
    ("rate-limit", "limite de taxa da plataforma"),
)


@dataclass(frozen=True)
class YtDlpSpec:
    """Configuração por plataforma para a implementação compartilhada."""

    platform: Platform
    allowed_extractors: tuple[str, ...]  # nomes/regex de extractor do yt-dlp — NUNCA "generic"
    extra_opts: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if any(name.lower() in ("generic", "all", "default") for name in self.allowed_extractors):
            raise ValueError("allowed_extractors não pode incluir o extractor genérico (SSRF).")


def _categorize_reason(message: str) -> str:
    lowered = message.lower()
    for keyword, reason in _REASON_KEYWORDS:
        if keyword in lowered:
            return reason
    return "falha na extração do vídeo"


class YtDlpDownloader(PlatformDownloader):
    def __init__(self, spec: YtDlpSpec) -> None:
        self.spec = spec
        self.platform = spec.platform

    def _build_options(self, dest_stub: Path, force_mweb: bool = False) -> dict:
        options = {
            "outtmpl": f"{dest_stub}.%(ext)s",
            "format": "mp4/best[ext=mp4]/best",
            "merge_output_format": "mp4",
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "restrictfilenames": True,
            "windowsfilenames": True,
            "trim_file_name": 200,
            # Segurança (ver docstring do módulo): só o(s) extractor(es) desta plataforma.
            "allowed_extractors": list(self.spec.allowed_extractors),
            "nocheckcertificate": False,
            "socket_timeout": DOWNLOAD_CONNECT_TIMEOUT_SECONDS,
            "max_filesize": MAX_VIDEO_SIZE_BYTES,
            "retries": 1,
            "fragment_retries": 1,
            "cookiefile": None,  # nunca cookies pessoais como padrão (especificação do produto)
            **self.spec.extra_opts,
        }
        self._apply_youtube_pot_provider(options, force_mweb=force_mweb)
        self._apply_youtube_proxy(options)
        self._normalize_impersonate_target(options)
        return options

    def _apply_youtube_proxy(self, options: dict) -> None:
        """Liga o proxy residencial (Decodo) só quando configurado (config.py) e só para
        YouTube -- mesmo guard de _apply_youtube_pot_provider, mesmo motivo: TikTok/Instagram/
        Pinterest nunca devem ser afetados por uma mudança feita para o YouTube.

        Diferente do BGUTIL (que só negocia o PO Token), aqui o proxy cobre TODA a requisição
        do yt-dlp para o YouTube -- extração da página, player API e o download do vídeo em si
        -- porque o diagnóstico de 02/10/2026 mostrou que o bloqueio ("Sign in to confirm
        you're not a bot" + HTTP 429) acontece já na primeira requisição (download da webpage),
        antes de qualquer client/PO-Token entrar em jogo. Trocar só uma parte da cadeia não
        resolveria nada; a chave 'proxy' do yt-dlp já cobre todas as chamadas HTTP feitas por
        ele (webpage, APIs e o arquivo de vídeo)."""
        proxy_url = get_settings().youtube_proxy_url
        if self.platform is not Platform.YOUTUBE or not proxy_url:
            return
        options["proxy"] = proxy_url

    def _normalize_impersonate_target(self, options: dict) -> None:
        """A CLI do yt-dlp converte a string de --impersonate para ImpersonateTarget antes de
        montar o YoutubeDL; a API Python (yt_dlp.YoutubeDL(options)) NÃO faz essa conversão
        sozinha — exige o objeto já pronto (senão: AssertionError em
        yt_dlp/networking/impersonate.py, isinstance(target, ImpersonateTarget)). Como
        extra_opts é genérico (qualquer plataforma pode declarar "impersonate", não só TikTok),
        essa normalização fica aqui, não em cada YtDlpSpec — string vira ImpersonateTarget; um
        ImpersonateTarget já pronto (ou ausência da chave) passa direto, sem alteração."""
        target = options.get("impersonate")
        if isinstance(target, str):
            options["impersonate"] = ImpersonateTarget.from_str(target)

    def _apply_youtube_pot_provider(self, options: dict, force_mweb: bool = False) -> None:
        """Liga o PO Token Provider (BGUTIL) só quando configurado (config.py) e só para YouTube.
        Sem isso, o yt-dlp segue com o client padrão sem PO Token: funciona para parte dos
        vídeos, e o que falhar vira DownloadFailedError diagnosticável (nunca uma pilha de
        fallbacks — regra do produto). PO Token não é garantia de acesso a todo vídeo.

        force_mweb (NOVO -- fallback, não é mais um comportamento sempre ligado): só quando True
        o client é forçado para "mweb". Por quê não é sempre True: um diagnóstico anterior já
        documentado neste projeto (download/service.py, config do YouTube, comentário de 26/09)
        mostrou, com teste real, que forçar "mweb" faz OUTROS vídeos (que hoje funcionam com a
        negociação automática do yt-dlp) devolverem só formatos de storyboard (sem vídeo/áudio
        de verdade). Por isso "mweb" só é usado como SEGUNDA tentativa (ver download()), quando a
        primeira tentativa (sem forçar nada -- comportamento ORIGINAL preservado) falha
        especificamente com o erro de login/bot-check do YouTube. Evidência de que "mweb" forçado
        funciona quando combinado com o BGUTIL: teste local controlado, mweb SEM player_skip +
        BGUTIL local -> log real mostrou "[pot:bgutil:http] Generating a gvs PO Token for mweb
        client via bgutil HTTP server" seguido de "Retrieved a gvs PO Token for mweb client".
        NÃO combinar com player_skip (webpage/configs): isso remove o Visitor Data exigido antes
        do pedido de PO Token e quebra a cadeia (confirmado em teste anterior, descartado por
        esse motivo).

        (02/10/2026: removido o logger verbose [DIAG-BGUTIL] que existia aqui -- era temporário,
        usado só para fechar o diagnóstico do bloqueio do YouTube, que já terminou: a causa era o
        IP de datacenter do Render, não client/PO-Token, ver YtDlpDownloader._apply_youtube_proxy.
        Mantido só o comportamento funcional, sem o log extra poluindo a produção.)"""
        base_url = get_settings().bgutil_pot_provider_base_url
        if self.platform is not Platform.YOUTUBE or not base_url:
            return
        extractor_args = dict(options.get("extractor_args") or {})
        youtube_args = dict(extractor_args.get("youtube") or {})
        if force_mweb:
            youtube_args["player_client"] = ["mweb"]
        extractor_args["youtubepot-bgutilhttp"] = {"base_url": [base_url]}
        extractor_args["youtube"] = youtube_args
        options["extractor_args"] = extractor_args

    def _run_extract(self, options: dict, url: str):
        with yt_dlp.YoutubeDL(options) as ydl:
            return ydl.extract_info(url, download=True)

    def _should_retry_with_mweb(self, reason: str) -> bool:
        """Segunda tentativa (mweb forçado + BGUTIL) só entra quando: é YouTube, o BGUTIL está
        configurado, e a 1a tentativa falhou especificamente por login/bot-check (a mesma
        categoria de "Sign in to confirm you're not a bot..."). Qualquer outro motivo de falha
        (vídeo privado, removido, URL inválida, timeout, etc.) não tem relação com PO Token e
        não deve gastar uma segunda tentativa -- o erro original já é o diagnóstico correto."""
        return (
            self.platform is Platform.YOUTUBE
            and bool(get_settings().bgutil_pot_provider_base_url)
            and reason == "conteúdo exige login na plataforma de origem"
        )

    def _error_for_reason(self, reason: str, exc: Exception):
        if "filesize" in str(exc).lower():
            return VideoTooLargeError(reason)
        if reason == "tempo de rede esgotado":
            return DownloadTimeoutError(reason)
        return DownloadFailedError(reason)

    def _cleanup_stub_files(self, dest_stub: Path) -> None:
        """Remove qualquer arquivo que a 1a tentativa possa ter deixado para trás (ex.: uma
        miniatura) antes da 2a tentativa -- sem isso, _resolve_output_path poderia pegar por
        engano um arquivo da tentativa que falhou em vez do vídeo baixado de verdade na retry."""
        directory = dest_stub.parent
        for stray in directory.glob(f"{dest_stub.name}.*"):
            try:
                stray.unlink()
            except OSError:
                pass

    def download(self, url: str, dest_stub: Path) -> RawDownload:
        options = self._build_options(dest_stub, force_mweb=False)
        try:
            info = self._run_extract(options, url)
        except yt_dlp.utils.DownloadError as exc:
            reason = _categorize_reason(str(exc))
            logger.warning("[DOWNLOAD] yt-dlp plataforma=%s motivo=%s", self.platform.value, reason)

            if not self._should_retry_with_mweb(reason):
                raise self._error_for_reason(reason, exc) from None

            logger.warning(
                "[DOWNLOAD] yt-dlp plataforma=%s retry com mweb+BGUTIL apos bot-check",
                self.platform.value,
            )
            self._cleanup_stub_files(dest_stub)
            retry_options = self._build_options(dest_stub, force_mweb=True)
            try:
                info = self._run_extract(retry_options, url)
            except yt_dlp.utils.DownloadError as exc2:
                reason2 = _categorize_reason(str(exc2))
                logger.warning(
                    "[DOWNLOAD] yt-dlp plataforma=%s motivo=%s (apos retry mweb+BGUTIL)",
                    self.platform.value, reason2,
                )
                raise self._error_for_reason(reason2, exc2) from None

        duration = info.get("duration") if isinstance(info, dict) else None
        path = self._resolve_output_path(dest_stub)
        return RawDownload(path=path, duration_seconds=float(duration) if duration else None)

    def _resolve_output_path(self, dest_stub: Path) -> Path:
        directory = dest_stub.parent
        matches = sorted(p for p in directory.glob(f"{dest_stub.name}.*") if p.suffix != ".part")
        if not matches:
            raise DownloadFailedError("arquivo de saída não encontrado após o download")
        return matches[0]
