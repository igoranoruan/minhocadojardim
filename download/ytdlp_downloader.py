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

    def _build_options(self, dest_stub: Path) -> dict:
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
        self._apply_youtube_pot_provider(options)
        return options

    def _apply_youtube_pot_provider(self, options: dict) -> None:
        """Liga o PO Token Provider (BGUTIL) só quando configurado (config.py) e só para YouTube.
        Sem isso, o yt-dlp segue com o player client mweb sem PO Token: funciona para parte dos
        vídeos, e o que falhar vira DownloadFailedError diagnosticável (nunca uma pilha de
        fallbacks — regra do produto). PO Token não é garantia de acesso a todo vídeo."""
        base_url = get_settings().bgutil_pot_provider_base_url
        if self.platform is not Platform.YOUTUBE or not base_url:
            return
        extractor_args = dict(options.get("extractor_args") or {})
        youtube_args = dict(extractor_args.get("youtube") or {})
        extractor_args["youtubepot-bgutilhttp"] = {"base_url": [base_url]}
        extractor_args["youtube"] = youtube_args
        options["extractor_args"] = extractor_args

    def download(self, url: str, dest_stub: Path) -> RawDownload:
        options = self._build_options(dest_stub)
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=True)
        except yt_dlp.utils.DownloadError as exc:
            reason = _categorize_reason(str(exc))
            logger.warning("[DOWNLOAD] yt-dlp plataforma=%s motivo=%s", self.platform.value, reason)
            if "filesize" in str(exc).lower():
                raise VideoTooLargeError(reason) from None
            if reason == "tempo de rede esgotado":
                raise DownloadTimeoutError(reason) from None
            raise DownloadFailedError(reason) from None

        duration = info.get("duration") if isinstance(info, dict) else None
        path = self._resolve_output_path(dest_stub)
        return RawDownload(path=path, duration_seconds=float(duration) if duration else None)

    def _resolve_output_path(self, dest_stub: Path) -> Path:
        directory = dest_stub.parent
        matches = sorted(p for p in directory.glob(f"{dest_stub.name}.*") if p.suffix != ".part")
        if not matches:
            raise DownloadFailedError("arquivo de saída não encontrado após o download")
        return matches[0]
