"""Ponto de entrada único da camada de download.

Fluxo: URL -> validação (url_safety) -> identificação da plataforma (platform) -> downloader
(ytdlp_downloader) -> arquivo temporário -> validação do arquivo (file_validation) -> resultado
padronizado (result.DownloadResult). Nada aqui conhece planos, gerações, pagamentos, sessão,
banco ou frontend — é consumido pela Etapa 6, nunca o contrário.

LIMITAÇÃO DE TIMEOUT (documentada): Python não tem uma forma segura de interromper uma thread no
meio de uma chamada bloqueante. O watchdog abaixo (ThreadPoolExecutor + future.result(timeout=...))
garante que o CHAMADOR nunca espera além de DOWNLOAD_TIMEOUT_SECONDS — isso foi CONFIRMADO por
teste (o executor é encerrado com shutdown(wait=False) no caminho de timeout; usar
`with ThreadPoolExecutor() as executor:` aqui seria um bug sutil, porque o __exit__ do context
manager bloqueia em shutdown(wait=True) e anularia o próprio watchdog — por isso o executor é
gerenciado manualmente). O que continua fora do nosso controle é a chamada ao yt-dlp em si, que
pode continuar rodando em segundo plano até terminar sozinha (mitigado por `socket_timeout`, que
evita travas indefinidas por um socket parado). Isso é aceitável para o escopo desta etapa:
nenhuma linha de `generations` é criada aqui (isso é Etapa 4, feito por quem chamar
`download_video`), então uma chamada "esquecida" em segundo plano não consome cota nem deixa o
produto em estado inconsistente — só ocupa uma thread até o próprio `socket_timeout` interno
encerrar a tentativa.
"""
import logging
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from urllib.parse import urlsplit

from config import DOWNLOAD_TIMEOUT_SECONDS, MAX_VIDEO_DURATION_SECONDS
from download.base import PlatformDownloader, RawDownload
from download.errors import (
    DownloadError,
    DownloadFailedError,
    DownloadTimeoutError,
    InvalidFileError,
    NoVideoInPostError,
    VideoTooLongError,
)
from download.file_validation import (
    detect_media_type,
    validate_downloaded_file,
    validate_downloaded_image,
    validate_downloaded_image_zip,
)
from download.image_fallback import fetch_post_image
from download.platform import Platform, detect_platform
from download.result import DownloadResult
from download.tempfiles import cleanup, new_temp_stub
from download.url_safety import resolve_redirect_chain, validate_url
from download.ytdlp_downloader import YtDlpDownloader, YtDlpSpec

logger = logging.getLogger("minhoca")

_SPECS: dict[Platform, YtDlpSpec] = {
    Platform.TIKTOK: YtDlpSpec(
        Platform.TIKTOK,
        allowed_extractors=("TikTok",),
        extra_opts={
            # TikTok exige um "desafio" (challenge) resolvido com fingerprint de navegador real
            # antes de servir a página do vídeo; sem isso, o yt-dlp falha com "Unexpected response
            # from webpage request" (ver _solve_challenge_and_set_cookies no extractor do TikTok).
            # "impersonate": "chrome" é o mesmo mecanismo do `--impersonate chrome` da CLI —
            # requer o pacote curl_cffi instalado (requirements.txt), que o yt-dlp usa
            # internamente como um Request Handler adicional (urllib/websockets continuam os
            # handlers padrão para as demais requisições; curl_cffi só entra quando um extractor
            # pede impersonation, como o TikTok passa a pedir aqui). Não temos cookies pessoais
            # envolvidos nisto — é só o fingerprint de TLS/HTTP do navegador sendo imitado, a
            # mesma requisição que qualquer visitante anônimo faria.
            "impersonate": "chrome",
            # format PRÓPRIO do TikTok (otimização de performance, 30/09 -- aprovação do
            # CÉREBRO): diferente do YouTube, o TikTok normalmente já entrega formats MUXADOS
            # (vídeo+áudio no mesmo arquivo, nunca streams DASH separados) -- por isso NÃO
            # precisamos de "bestvideo+bestaudio" aqui, só de preferir, entre os formats muxados
            # já existentes, o que já vem em H.264 (e, se possível, AAC/M4A) -- os únicos codecs
            # que processor/service.py aceita para `-c:v copy`/`-c:a copy`.
            #
            # CAUSA RAIZ CONFIRMADA (aprovação do CÉREBRO, 30/09 -- URL real testada:
            # tiktok.com/@cazetv/video/7659614093048909064): a primeira tentativa desta otimização
            # usava "vcodec^=avc" (nomenclatura do YouTube), mas o extractor do TikTok reporta o
            # codec H.264 literalmente como "h264" (yt-dlp -F real: "h264_540p... h264 aac",
            # nunca "avc1...") -- o filtro "vcodec^=avc" NUNCA casava com nenhum format do TikTok,
            # e o fallback sem filtro escolhia o "best" entre TODOS os formats disponíveis
            # (incluindo "bytevc1_1080p..." reportado como vcodec="h265"/HEVC) -- resultando no
            # arquivo real baixado ser 1080x1918 HEVC+AAC, nunca elegível para `-c:v copy`, e
            # forçando transcode completo (~178-233s).
            #
            # CORREÇÃO: filtrar por "h264" (a nomenclatura REAL que o TikTok reporta), com "avc"
            # mantido como alternativa defensiva logo em seguida (mesma nomenclatura do YouTube --
            # cobre uma eventual mudança futura de nomenclatura do extractor do TikTok, sem custo
            # nenhum quando "h264" já casa primeiro, que é o caso hoje). "best[vcodec^=h264]
            # [acodec^=mp4a]" -- primeiro tenta o melhor format muxado já H.264+AAC. "best
            # [vcodec^=avc][acodec^=mp4a]" -- mesma coisa, nomenclatura alternativa. "best
            # [vcodec^=h264]"/"best[vcodec^=avc]" -- aceitam H.264 com qualquer áudio (processor
            # usa copy_video_transcode_audio se o áudio não for AAC). Em TODOS os quatro, "best"
            # (sem filtro de altura/resolução) escolhe o MELHOR format que casar o filtro de codec
            # -- no vídeo de teste real, isso é o format "play" 1080x1918 H.264+AAC, NUNCA um
            # H.264 de qualidade inferior só para caber no filtro (não reduz resolução
            # propositalmente). "mp4/best[ext=mp4]/best" -- ÚLTIMO fallback, EXATAMENTE IDÊNTICO
            # ao format usado antes desta otimização: nenhum vídeo sem NENHUM format H.264
            # disponível baixa diferente do que já baixava.
            "format": (
                "best[vcodec^=h264][acodec^=mp4a]"
                "/best[vcodec^=avc][acodec^=mp4a]"
                "/best[vcodec^=h264]"
                "/best[vcodec^=avc]"
                "/mp4/best[ext=mp4]/best"
            ),
        },
    ),
    # Instagram: AUDITADO nesta rodada (30/09, junto com TikTok/Pinterest) e PRESERVADO sem
    # nenhuma mudança de format -- evidência real (URL real testada:
    # instagram.com/reel/DbbnF6APswb/, aprovação do CÉREBRO) confirmou que o downloader atual já
    # produz H.264+AAC 720x1280, plenamente compatível com o caminho `copy`. O format compartilhado
    # ("mp4/best[ext=mp4]/best") já é adequado aqui, mesmo o Instagram também oferecendo formats
    # DASH -- diferente do TikTok (nomenclatura de codec incompatível com o filtro) e do Pinterest
    # (formato escolhido sem áudio), o Instagram não apresentou nenhum dos dois problemas na
    # evidência real. Nenhuma mudança "para melhorar o que já funciona" -- só altera o que está
    # comprovadamente quebrado.
    Platform.INSTAGRAM: YtDlpSpec(Platform.INSTAGRAM, allowed_extractors=("Instagram",)),
    Platform.PINTEREST: YtDlpSpec(
        Platform.PINTEREST,
        allowed_extractors=("Pinterest",),
        extra_opts={
            # format PRÓPRIO do Pinterest (otimização de performance, 30/09 -- aprovação do
            # CÉREBRO). CAUSA RAIZ CONFIRMADA (URL real testada: pin.it/6i84tmn2E): o Pinterest
            # serve HLS com vídeo e áudio em formats SEPARADOS (yt-dlp -F real:
            # "V_HLSV3_MOBILE-703" 720x1280 avc1 vídeo-only + "V_HLSV3_MOBILE-audio1-1" áudio-only)
            # -- nunca um format já muxado com os dois juntos. O format compartilhado
            # ("mp4/best[ext=mp4]/best") não pede nenhum merge (diferente do YouTube/TikTok, que
            # usam "bestvideo+bestaudio" ou já recebem formats muxados) -- "best[ext=mp4]" casava
            # com o MELHOR format vídeo-only cujo container reportado já é mp4 (comum em segmentos
            # HLS), SEM NUNCA considerar se existia áudio -- resultado real confirmado: MP4
            # H.264 720x1280 13.56s, mas SEM NENHUMA faixa de áudio (o processor então usava
            # `copy` -- rápido, ~1.99s -- mas produzindo um vídeo MUDO, um bug de correção, não
            # só de performance).
            #
            # CORREÇÃO (genérica, SEM hardcodar os IDs "V_HLSV3_MOBILE-703"/"V_HLSV3_MOBILE-
            # audio1-1" -- específicos deste pin, nunca reaproveitáveis por outro): usar
            # "bestvideo+bestaudio", que EXIGE que o yt-dlp resolva e faça o merge de um stream de
            # vídeo COM um stream de áudio -- nunca aceita implicitamente um format vídeo-only
            # como se fosse completo (diferente do "best[ext=mp4]" antigo). "bestvideo[vcodec^=avc]
            # +bestaudio" -- primeiro tenta o melhor vídeo-only em H.264/AVC (nomenclatura "avc1"
            # confirmada pela evidência real desta plataforma) com o melhor áudio-only disponível
            # (merge do yt-dlp já produz H.264 -- processor usa `copy` se o áudio vier AAC, ou
            # `copy_video_transcode_audio` se não vier). "bestvideo[vcodec^=h264]+bestaudio" --
            # nomenclatura alternativa defensiva (mesmo raciocínio do TikTok, sem custo quando
            # "avc" já casa primeiro). "bestvideo+bestaudio" (sem filtro de codec) -- fallback para
            # quando não houver NENHUM vídeo H.264/AVC disponível -- ainda assim SEMPRE com áudio,
            # nunca repete o bug de origem. "/best" -- ÚLTIMO recurso absoluto, só para o caso raro
            # de não existir nenhum par vídeo+áudio separável (um format já completo sozinho).
            # merge_output_format="mp4" (compartilhado, já existente) garante que o resultado do
            # merge sai como .mp4 em qualquer um dos casos acima.
            "format": (
                "bestvideo[vcodec^=avc]+bestaudio"
                "/bestvideo[vcodec^=h264]+bestaudio"
                "/bestvideo+bestaudio"
                "/best"
            ),
        },
    ),
    Platform.YOUTUBE: YtDlpSpec(
        Platform.YOUTUBE,
        allowed_extractors=("Youtube",),
        extra_opts={
            # format PRÓPRIO do YouTube (sobrescreve o "mp4/best[ext=mp4]/best" compartilhado só
            # para esta plataforma — diagnóstico do erro real em Shorts, 26/09): muitos vídeos/
            # Shorts só expõem streams DASH separados (vídeo-only + áudio-only), sem NENHUM format
            # já muxado. "best"/"best[ext=mp4]" (sem "*") só casam com format único que já tenha
            # vídeo E áudio juntos -- por isso os três fallbacks antigos se esgotavam sem candidato
            # ("Requested format is not available"). "bestvideo+bestaudio" pede ao yt-dlp para
            # casar o melhor vídeo-only com o melhor áudio-only e fazer o merge (via ffmpeg, que o
            # yt-dlp já localiza sozinho no PATH).
            #
            # Otimização de performance (caminho rápido / stream copy, 30/09 -- aprovação do
            # CÉREBRO): ANTES dos fallbacks sem filtro, tentamos casar vcodec=avc (H.264) com
            # acodec=mp4a (AAC/M4A) -- exatamente os únicos codecs que processor/service.py aceita
            # para o caminho `-c:v copy` (ver _COPY_SAFE_VIDEO_CODECS/_COPY_SAFE_AUDIO_CODECS).
            # Quando o YouTube oferece essa combinação (muito comum: itags 137/136/135/... vídeo +
            # 140 áudio), o merge do yt-dlp já sai H.264+AAC e o processor nem precisa reencodar
            # vídeo. "^=avc"/"^=mp4a" casam tanto "avc1" quanto "avc1.640028" (idem mp4a.40.2) --
            # prefixo, não igualdade exata, porque o yt-dlp reporta o codec com o perfil/nível
            # junto. NÃO restringimos altura/resolução aqui: se o YouTube não tiver H.264 na MESMA
            # qualidade que o VP9/AV1 equivalente, isso é decidido pelo próprio critério de
            # ordenação padrão do yt-dlp dentro do filtro (ainda escolhe o "melhor" H.264
            # disponível, nunca um H.264 pior só para caber no filtro). "bestvideo+bestaudio/best"
            # continua como ÚLTIMO fallback, IDÊNTICO ao comportamento anterior a esta mudança --
            # nenhum vídeo deixa de baixar por causa deste filtro nem fica pior do que já ficava:
            # na ausência de H.264+AAC, o yt-dlp cai exatamente no que já fazia antes.
            "format": (
                "bestvideo[vcodec^=avc]+bestaudio[acodec^=mp4a]"
                "/best[vcodec^=avc][acodec^=mp4a]"
                "/bestvideo+bestaudio/best"
            ),
            # NÃO forçar player_client (diagnóstico real em Windows, 26/09): "mweb" (e "web") só
            # devolviam formatos de storyboard (sb0/mhtml) para este vídeo -- nenhum vídeo/áudio de
            # verdade. Sem player_client forçado, o yt-dlp negocia os clients padrão sozinho e
            # recebeu os formatos reais (140/299/303/399/etc.), confirmado por teste isolado
            # (yt-dlp -F e download real bem-sucedido). Não reintroduzir "mweb"/BGUTIL aqui sem um
            # novo diagnóstico -- isso é responsabilidade de uma etapa própria, não desta correção.
        },
    ),
}

_DOWNLOADERS: dict[Platform, PlatformDownloader] = {
    platform: YtDlpDownloader(spec) for platform, spec in _SPECS.items()
}

# Domínios de link curto que precisam ser resolvidos ANTES de chegar ao yt-dlp -- ver o comentário
# dentro de download_video, abaixo, para a causa raiz completa.
_SHORT_LINK_HOSTS: dict[Platform, frozenset[str]] = {
    Platform.PINTEREST: frozenset({"pin.it"}),
    Platform.TIKTOK: frozenset({"vm.tiktok.com", "vt.tiktok.com"}),
}


def download_video(url: str) -> DownloadResult:
    """Baixa e valida um vídeo. Levanta DownloadError (ou subclasse) em qualquer falha; nesse
    caso, todo arquivo temporário já criado é removido antes de propagar o erro."""
    validated = validate_url(url)
    platform = detect_platform(validated.url)

    # Encurtadores: pin.it (Pinterest) e vm.tiktok.com/vt.tiktok.com (TikTok) são links curtos cujo
    # extractor oficial do yt-dlp só reconhece a URL LONGA (pinterest.com/pin/... ou
    # tiktok.com/@usuario/video/...) -- é o extractor genérico do yt-dlp quem resolveria o
    # redirecionamento sozinho, e esse extractor está deliberadamente fora de allowed_extractors
    # (ver download/ytdlp_downloader.py). vt.tiktok.com confirmado em produção (02/10/2026): yt-dlp
    # devolvia "No suitable extractor found for URL" direto, mesmo com o host já reconhecido como
    # TikTok em download/platform.py -- mesma causa raiz do pin.it, resolvida da mesma forma. Por
    # isso resolvemos o link curto NÓS MESMOS, com a mesma função já usada para isso (
    # resolve_redirect_chain: nunca segue automático, revalida cada salto contra IP privado/SSRF,
    # só HEAD, limite de saltos) — sem nunca abrir allowed_extractors. URLs já longas (pinterest.com
    # ou tiktok.com diretas) não passam por aqui.
    host = (urlsplit(validated.url).hostname or "").lower()
    if host in _SHORT_LINK_HOSTS.get(platform, frozenset()):
        validated = resolve_redirect_chain(validated.url)

    downloader = _DOWNLOADERS[platform]
    stub = new_temp_stub()

    try:
        try:
            raw = _download_with_timeout(lambda: downloader.download(validated.url, stub))
        except NoVideoInPostError:
            # Post/pin sem vídeo nenhum (só imagem) -- TikTok, Instagram e Pinterest (03/10/2026,
            # aprovação do CÉREBRO). O yt-dlp já confirmou (download/ytdlp_downloader.py) que não
            # há vídeo; tentamos ler a imagem publicada na própria página pública do post (
            # download/image_fallback.py) ANTES de desistir. Nenhuma mudança de comportamento para
            # qualquer outro motivo de falha -- este bloco só é alcançado por esta exceção
            # específica, nunca por vídeo privado/removido/timeout/etc.
            raw = _download_with_timeout(lambda: fetch_post_image(validated.url, stub))
        _check_duration(raw)
        media_type = detect_media_type(raw.path)
        if media_type == "video":
            size = validate_downloaded_file(raw.path)
        elif media_type == "image":
            size = validate_downloaded_image(raw.path)
        elif media_type == "image_zip":
            size = validate_downloaded_image_zip(raw.path)
        else:
            raise InvalidFileError(f"extensão não reconhecida: {raw.path.suffix!r}")
        return DownloadResult(
            platform=platform,
            temp_path=raw.path,
            size_bytes=size,
            duration_seconds=raw.duration_seconds,
            container_format=raw.path.suffix.lstrip("."),
            media_type=media_type,
        )
    except DownloadError:
        cleanup(stub)
        raise
    except Exception as exc:
        cleanup(stub)
        logger.error(
            "[DOWNLOAD] falha inesperada plataforma=%s tipo=%s", platform.value, exc.__class__.__name__,
            exc_info=True,
        )
        raise DownloadFailedError() from None


def _check_duration(raw: RawDownload) -> None:
    if raw.duration_seconds is not None and raw.duration_seconds > MAX_VIDEO_DURATION_SECONDS:
        raise VideoTooLongError(f"{raw.duration_seconds}s > {MAX_VIDEO_DURATION_SECONDS}s (informado pela plataforma)")


def _download_with_timeout(fn) -> RawDownload:
    """`fn` é uma chamada sem argumentos que devolve RawDownload (um `downloader.download(url,
    stub)` já fechado em lambda, ou -- fallback de imagem, 03/10/2026 -- um `fetch_post_image(url,
    stub)` já fechado do mesmo jeito): o watchdog abaixo é o mesmo para os dois casos, nenhuma
    mudança de comportamento/timeout para o caminho de vídeo."""
    # NÃO usar `with ThreadPoolExecutor() as executor:` aqui: o __exit__ do context manager chama
    # shutdown(wait=True), que bloqueia até a thread terminar — ou seja, mesmo depois do timeout
    # abaixo, o chamador ficaria esperando o download lento terminar de qualquer jeito, anulando
    # o watchdog. Por isso o executor é criado e encerrado à mão, com wait=False no caminho de
    # timeout (só nesse caminho o chamador realmente precisa não esperar).
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(fn)
    try:
        result = future.result(timeout=DOWNLOAD_TIMEOUT_SECONDS)
    except FutureTimeoutError:
        executor.shutdown(wait=False)
        raise DownloadTimeoutError() from None
    else:
        executor.shutdown(wait=True)  # a tarefa já terminou: isto não bloqueia de fato
        return result
