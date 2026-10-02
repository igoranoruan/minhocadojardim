"""Orquestração ponta a ponta (URL -> validação -> plataforma -> downloader -> validação do
arquivo -> DownloadResult), usando um PlatformDownloader falso — nunca chama TikTok/Instagram/
Pinterest/YouTube de verdade.
"""
import time
from pathlib import Path
from unittest.mock import patch

import pytest

import download.service as service_module
from download.base import PlatformDownloader, RawDownload
from download.errors import (
    DownloadFailedError,
    DownloadTimeoutError,
    InvalidFileError,
    SsrfBlockedError,
    UnsupportedPlatformError,
    VideoTooLargeError,
    VideoTooLongError,
)
from download.platform import Platform
from download.result import DownloadResult
from download.service import download_video
from download.url_safety import ValidatedUrl


class FakeDownloader(PlatformDownloader):
    """Downloader de mentira: grava bytes no stub, sem rede nem yt-dlp."""

    def __init__(self, platform, *, content=b"video", extension="mp4", duration=None, delay=0.0, error=None):
        self.platform = platform
        self.content = content
        self.extension = extension
        self.duration = duration
        self.delay = delay
        self.error = error
        self.calls: list[str] = []

    def download(self, url: str, dest_stub: Path) -> RawDownload:
        self.calls.append(url)
        if self.delay:
            time.sleep(self.delay)
        if self.error is not None:
            raise self.error
        path = dest_stub.with_suffix(f".{self.extension}")
        path.write_bytes(self.content)
        return RawDownload(path=path, duration_seconds=self.duration)


@pytest.fixture()
def registry(monkeypatch, tmp_path):
    """Substitui o registro de downloaders e o diretório temporário por uma versão de teste."""
    fakes: dict[Platform, FakeDownloader] = {p: FakeDownloader(p) for p in Platform}
    monkeypatch.setattr(service_module, "_DOWNLOADERS", fakes)
    monkeypatch.setattr("download.tempfiles.DOWNLOAD_TEMP_DIR", str(tmp_path))
    return fakes


def _sem_ssrf(monkeypatch):
    """As URLs de teste usam domínios reais (tiktok.com etc.); evitamos depender de DNS real."""
    monkeypatch.setattr("download.url_safety.resolve_host_ips", lambda host: ("93.184.216.34",))


# ------------------------------------------------------------------ caminho feliz
def test_download_com_sucesso(registry, monkeypatch):
    _sem_ssrf(monkeypatch)
    registry[Platform.TIKTOK].duration = 30.0
    resultado = download_video("https://www.tiktok.com/@a/video/1")
    assert isinstance(resultado, DownloadResult)
    assert resultado.platform is Platform.TIKTOK
    assert resultado.temp_path.exists()
    assert resultado.size_bytes == len(b"video")
    assert resultado.duration_seconds == 30.0
    assert resultado.container_format == "mp4"
    assert registry[Platform.TIKTOK].calls == ["https://www.tiktok.com/@a/video/1"]


def test_cada_plataforma_usa_o_downloader_correto(registry, monkeypatch):
    _sem_ssrf(monkeypatch)
    casos = {
        "https://www.tiktok.com/@a/video/1": Platform.TIKTOK,
        "https://instagram.com/reel/abc/": Platform.INSTAGRAM,
        "https://pinterest.com/pin/123/": Platform.PINTEREST,
        "https://youtu.be/abc123": Platform.YOUTUBE,
    }
    for url, plataforma in casos.items():
        resultado = download_video(url)
        assert resultado.platform is plataforma
        assert registry[plataforma].calls[-1] == url


# ------------------------------------------------------------------ recusas antes de chamar o downloader
def test_url_de_dominio_nao_suportado_nao_chama_nenhum_downloader(registry, monkeypatch):
    _sem_ssrf(monkeypatch)
    with pytest.raises(UnsupportedPlatformError):
        download_video("https://example.com/video")
    assert all(fake.calls == [] for fake in registry.values())


def test_url_com_ssrf_nao_chega_a_identificar_plataforma(registry, monkeypatch):
    with pytest.raises(SsrfBlockedError):
        download_video("http://169.254.169.254/tiktok.com")
    assert all(fake.calls == [] for fake in registry.values())


# ------------------------------------------------------------------ validações pós-download
def test_duracao_informada_acima_do_limite_e_recusada_e_arquivo_e_limpo(registry, monkeypatch, tmp_path):
    _sem_ssrf(monkeypatch)
    registry[Platform.YOUTUBE].duration = 301.0  # > 5 min
    with pytest.raises(VideoTooLongError):
        download_video("https://youtu.be/abc123")
    assert list(tmp_path.iterdir()) == []  # nada sobrou


def test_duracao_ausente_nao_bloqueia_o_download(registry, monkeypatch):
    _sem_ssrf(monkeypatch)
    registry[Platform.YOUTUBE].duration = None  # validação definitiva fica para a Etapa 6
    resultado = download_video("https://youtu.be/abc123")
    assert resultado.duration_seconds is None


def test_arquivo_maior_que_o_limite_e_recusado_e_limpo(registry, monkeypatch, tmp_path):
    _sem_ssrf(monkeypatch)
    monkeypatch.setattr("download.file_validation.MAX_VIDEO_SIZE_BYTES", 3)
    registry[Platform.TIKTOK].content = b"video-grande"
    with pytest.raises(VideoTooLargeError):
        download_video("https://www.tiktok.com/@a/video/1")
    assert list(tmp_path.iterdir()) == []


def test_extensao_de_saida_nao_permitida_e_recusada_e_limpa(registry, monkeypatch, tmp_path):
    _sem_ssrf(monkeypatch)
    registry[Platform.TIKTOK].extension = "exe"
    with pytest.raises(InvalidFileError):
        download_video("https://www.tiktok.com/@a/video/1")
    assert list(tmp_path.iterdir()) == []


# ------------------------------------------------------------------ erro do downloader propaga limpo
def test_erro_do_downloader_e_propagado_e_limpa_parciais(registry, monkeypatch, tmp_path):
    _sem_ssrf(monkeypatch)

    def baixa_e_falha(url, dest_stub):
        dest_stub.with_suffix(".mp4.part").write_bytes(b"parcial")
        raise DownloadFailedError("falha simulada")

    registry[Platform.TIKTOK].download = baixa_e_falha
    with pytest.raises(DownloadFailedError):
        download_video("https://www.tiktok.com/@a/video/1")
    assert list(tmp_path.iterdir()) == []  # o .part também foi removido


def test_excecao_inesperada_vira_downloadfailederror_generico(registry, monkeypatch):
    _sem_ssrf(monkeypatch)

    def explode(url, dest_stub):
        raise RuntimeError("bug interno com detalhe sensível=segredo123")

    registry[Platform.TIKTOK].download = explode
    with pytest.raises(DownloadFailedError) as capturado:
        download_video("https://www.tiktok.com/@a/video/1")
    assert "segredo123" not in capturado.value.user_message


# ------------------------------------------------------------------ links curtos (pin.it, vm/vt.tiktok.com)
def _validated(url: str) -> ValidatedUrl:
    return ValidatedUrl(url=url, scheme="https", host=url.split("/")[2], port=443, resolved_ips=("93.184.216.34",))


def test_pin_it_chama_resolve_redirect_chain_e_envia_a_url_longa_ao_downloader(registry, monkeypatch):
    _sem_ssrf(monkeypatch)
    url_longa = "https://www.pinterest.com/pin/123456789/"
    with patch("download.service.resolve_redirect_chain", return_value=_validated(url_longa)) as mock_resolve:
        resultado = download_video("https://pin.it/5h9Mozm6x")

    mock_resolve.assert_called_once_with("https://pin.it/5h9Mozm6x")
    assert resultado.platform is Platform.PINTEREST
    assert registry[Platform.PINTEREST].calls == [url_longa]  # o downloader recebeu a URL LONGA, não a curta


def test_pinterest_com_direto_nao_chama_resolve_redirect_chain(registry, monkeypatch):
    _sem_ssrf(monkeypatch)
    with patch("download.service.resolve_redirect_chain") as mock_resolve:
        download_video("https://pinterest.com/pin/123456789/")
    mock_resolve.assert_not_called()
    assert registry[Platform.PINTEREST].calls == ["https://pinterest.com/pin/123456789/"]


def test_urls_ja_longas_nao_chamam_resolve_redirect_chain(registry, monkeypatch):
    """URLs que já chegam no formato longo (inclusive tiktok.com/www.tiktok.com direto, diferente
    de vm.tiktok.com/vt.tiktok.com abaixo) não passam pela resolução de link curto -- só os hosts
    em _SHORT_LINK_HOSTS (pin.it, vm.tiktok.com, vt.tiktok.com) usam."""
    _sem_ssrf(monkeypatch)
    for url in ("https://www.tiktok.com/@a/video/1", "https://instagram.com/reel/abc/", "https://youtu.be/abc123"):
        with patch("download.service.resolve_redirect_chain") as mock_resolve:
            download_video(url)
        mock_resolve.assert_not_called()


@pytest.mark.parametrize("host_curto", ["vm.tiktok.com", "vt.tiktok.com"])
def test_tiktok_link_curto_chama_resolve_redirect_chain_e_envia_a_url_longa_ao_downloader(
    registry, monkeypatch, host_curto
):
    """CAUSA RAIZ CONFIRMADA EM PRODUÇÃO (02/10/2026): vt.tiktok.com (link curto gerado pelo botão
    "Compartilhar" do app) devolvia "No suitable extractor found for URL" do yt-dlp, mesmo com o
    host já reconhecido como TikTok em download/platform.py -- o extractor "TikTok" só reconhece a
    URL longa (tiktok.com/@usuario/video/...), igual ao pin.it do Pinterest. Mesma correção."""
    _sem_ssrf(monkeypatch)
    url_longa = "https://www.tiktok.com/@usuario/video/7659614093048909064"
    with patch("download.service.resolve_redirect_chain", return_value=_validated(url_longa)) as mock_resolve:
        resultado = download_video(f"https://{host_curto}/ZSb5f7NnW/")

    mock_resolve.assert_called_once_with(f"https://{host_curto}/ZSb5f7NnW/")
    assert resultado.platform is Platform.TIKTOK
    assert registry[Platform.TIKTOK].calls == [url_longa]  # o downloader recebeu a URL LONGA, não a curta


def test_tiktok_link_curto_redirecionando_para_ip_privado_continua_bloqueado(registry, monkeypatch):
    """Mesma garantia SSRF do pin.it (teste abaixo) -- resolve_redirect_chain já é SSRF-safe, o
    downloader nunca chega a ser chamado se o redirecionamento apontar para IP privado."""
    _sem_ssrf(monkeypatch)
    with patch("download.service.resolve_redirect_chain", side_effect=SsrfBlockedError("host resolve para IP privado")):
        with pytest.raises(SsrfBlockedError):
            download_video("https://vt.tiktok.com/malicioso/")
    assert registry[Platform.TIKTOK].calls == []


def test_allowed_extractors_do_pinterest_continua_sem_generic():
    import download.service as service_module
    spec = service_module._SPECS[Platform.PINTEREST]
    assert spec.allowed_extractors == ("Pinterest",)
    assert "generic" not in [e.lower() for e in spec.allowed_extractors]


def test_pin_it_redirecionando_para_ip_privado_continua_bloqueado(registry, monkeypatch):
    """A resolução de pin.it usa resolve_redirect_chain, que já é SSRF-safe (Etapa 5) -- este
    teste confirma que o bloqueio propaga corretamente pelo caminho novo, sem o downloader
    chegar a ser chamado."""
    _sem_ssrf(monkeypatch)
    with patch("download.service.resolve_redirect_chain", side_effect=SsrfBlockedError("host resolve para IP privado")):
        with pytest.raises(SsrfBlockedError):
            download_video("https://pin.it/malicioso")
    assert registry[Platform.PINTEREST].calls == []


# ------------------------------------------------------------------ impersonation do TikTok (curl_cffi)
def test_tiktok_esta_configurado_com_impersonate_chrome():
    """O SPEC declara a string "chrome" -- legível, fácil de configurar/testar. A normalização
    para o tipo que o yt-dlp realmente exige acontece depois, em _build_options()
    (ver test_tiktok_impersonate_e_entregue_como_impersonatetarget_ao_ydl_opts abaixo)."""
    import download.service as service_module
    spec = service_module._SPECS[Platform.TIKTOK]
    assert spec.extra_opts.get("impersonate") == "chrome"


def test_tiktok_impersonate_e_entregue_como_impersonatetarget_ao_ydl_opts(tmp_path):
    """A API Python do yt_dlp.YoutubeDL(options) NÃO converte a string "chrome" sozinha (só a
    CLI faz essa conversão) -- exige um ImpersonateTarget já pronto, senão AssertionError dentro
    do próprio yt-dlp. Este teste confirma, com o YtDlpDownloader e o YtDlpSpec REAIS de produção
    (não um dublê), que options["impersonate"] chega como o tipo certo."""
    from yt_dlp.networking.impersonate import ImpersonateTarget

    from download.ytdlp_downloader import YtDlpDownloader

    downloader = YtDlpDownloader(service_module._SPECS[Platform.TIKTOK])
    options = downloader._build_options(tmp_path / "stub")
    assert isinstance(options["impersonate"], ImpersonateTarget)
    assert not isinstance(options["impersonate"], str)
    assert options["impersonate"] == ImpersonateTarget.from_str("chrome")


def test_impersonate_nao_e_aplicado_indevidamente_as_outras_plataformas(tmp_path):
    from download.ytdlp_downloader import YtDlpDownloader

    for plataforma in (Platform.INSTAGRAM, Platform.PINTEREST, Platform.YOUTUBE):
        spec = service_module._SPECS[plataforma]
        assert "impersonate" not in spec.extra_opts, plataforma
        # também confere as opções FINAIS construídas, não só a declaração crua do spec
        options = YtDlpDownloader(spec)._build_options(tmp_path / "stub")
        assert "impersonate" not in options, plataforma


def test_tiktok_allowed_extractors_continua_intacto_com_impersonate():
    """A restrição de segurança (allowed_extractors) não foi afetada pela config de impersonation."""
    import download.service as service_module
    spec = service_module._SPECS[Platform.TIKTOK]
    assert spec.allowed_extractors == ("TikTok",)
    assert "generic" not in [e.lower() for e in spec.allowed_extractors]


def test_youtube_nao_forca_mais_player_client():
    """Diagnóstico real em Windows (26/09): "mweb" (e "web") só devolviam formatos de storyboard
    (sb0/mhtml) para o Short testado -- nenhum vídeo/áudio de verdade. Sem player_client forçado,
    o yt-dlp negocia os clients padrão sozinho e recebeu os formatos reais (confirmado por teste
    isolado com download real). A spec do YouTube não deve mais declarar "extractor_args"."""
    import download.service as service_module
    spec = service_module._SPECS[Platform.YOUTUBE]
    assert "extractor_args" not in spec.extra_opts
    assert "player_client" not in str(spec.extra_opts)  # nenhum vestígio, nem aninhado


# ------------------------------------------------------------------ format do YouTube (diagnóstico 26/09: Shorts sem format muxado)
_FORMAT_YOUTUBE_ESPERADO = (
    "bestvideo[vcodec^=avc]+bestaudio[acodec^=mp4a]"
    "/best[vcodec^=avc][acodec^=mp4a]"
    "/bestvideo+bestaudio/best"
)


def test_youtube_usa_bestvideo_mais_bestaudio_como_format():
    """O format compartilhado ("mp4/best[ext=mp4]/best") só casa com um format ÚNICO já
    combinado (vídeo+áudio no mesmo stream) -- por isso falhava quando o YouTube (via mweb)
    só oferecia streams DASH separados. O YouTube agora sobrescreve "format" no seu próprio
    extra_opts, pedindo explicitamente o merge de vídeo-only + áudio-only.

    Otimização de performance (caminho rápido / stream copy, 30/09 -- aprovação do CÉREBRO): o
    format passou a tentar PRIMEIRO H.264 (vcodec^=avc) + AAC/M4A (acodec^=mp4a) -- os únicos
    codecs que processor/service.py aceita para `-c:v copy`/`-c:a copy` -- caindo para o
    "bestvideo+bestaudio/best" de sempre (IDÊNTICO ao comportamento anterior) só quando essa
    combinação não existir. Isso não é "validado" só por este teste passar: exige confirmação
    com download real (yt-dlp contra YouTube de verdade) antes de considerar a mudança fechada."""
    from download.ytdlp_downloader import YtDlpDownloader

    spec = service_module._SPECS[Platform.YOUTUBE]
    assert spec.extra_opts.get("format") == _FORMAT_YOUTUBE_ESPERADO

    # também confere as opções FINAIS construídas, não só a declaração crua do spec
    options = YtDlpDownloader(spec)._build_options(Path("/tmp/stub"))
    assert options["format"] == _FORMAT_YOUTUBE_ESPERADO
    # o merge continua saindo como .mp4 (opção compartilhada, não tocada por esta mudança)
    assert options["merge_output_format"] == "mp4"


def test_youtube_prefere_h264_aac_mas_mantem_fallback_sem_filtro_por_ultimo():
    """O NOVO filtro (H.264+AAC) vem só como PRIMEIRA opção da cadeia "/" -- o último ELO
    continua sendo, literalmente, "bestvideo+bestaudio/best" -- o mesmo format usado (sozinho,
    sem nenhum filtro de codec) antes desta mudança. Isso garante que um vídeo sem NENHUM stream
    H.264+AAC disponível baixa exatamente como baixava antes (nunca fica pior, nunca deixa de
    baixar por causa do filtro novo).

    CORREÇÃO (aprovação do CÉREBRO, pós-teste): `formato.split("/")` está ERRADO para isolar os
    elos da cadeia -- "/" é ao mesmo tempo o separador ENTRE elos e um caractere que aparece
    DENTRO do próprio fallback "bestvideo+bestaudio/best" (ele tem um "/" no meio). Um split()
    ingênuo quebra esse elo final em dois pedaços ("bestvideo+bestaudio" e "best"), fazendo
    `alternativas[-1]` virar só "best" -- nunca bate com a string completa do fallback, e o teste
    falhava por um bug NELE MESMO, não por nenhum problema no seletor de format real. A forma
    robusta é checar o SUFIXO literal da string inteira (`str.endswith`), que não tem esse
    problema de ambiguidade do separador."""
    spec = service_module._SPECS[Platform.YOUTUBE]
    formato = spec.extra_opts["format"]
    assert formato.endswith("/bestvideo+bestaudio/best")  # fallback final, literal, sem filtro
    primeiro_elo = formato.split("/", 1)[0]  # só o PRIMEIRO "/" -- isola o 1º elo sem quebrar o fallback
    assert "vcodec^=avc" in primeiro_elo and "acodec^=mp4a" in primeiro_elo


def test_instagram_continua_com_o_format_compartilhado():
    """Instagram não declara "format" próprio -- deve continuar herdando o valor compartilhado de
    ytdlp_downloader.py, sem nenhuma influência das mudanças do YouTube, TikTok ou Pinterest
    (auditado nesta rodada, 30/09, com URL real -- instagram.com/reel/DbbnF6APswb/ -- e
    PRESERVADO sem alteração de format: já produz H.264+AAC compatível com `copy`)."""
    from download.ytdlp_downloader import YtDlpDownloader

    spec = service_module._SPECS[Platform.INSTAGRAM]
    assert "format" not in spec.extra_opts
    options = YtDlpDownloader(spec)._build_options(Path("/tmp/stub"))
    assert options["format"] == "mp4/best[ext=mp4]/best"


# ------------------------------------------------------------------ format do TikTok (correção real, 30/09 -- causa raiz: yt-dlp reporta o H.264 do TikTok como "h264", nunca "avc1")
_FORMAT_TIKTOK_ESPERADO = (
    "best[vcodec^=h264][acodec^=mp4a]"
    "/best[vcodec^=avc][acodec^=mp4a]"
    "/best[vcodec^=h264]"
    "/best[vcodec^=avc]"
    "/mp4/best[ext=mp4]/best"
)


def test_tiktok_prefere_h264_mas_mantem_fallback_identico_ao_anterior_por_ultimo():
    """CAUSA RAIZ CONFIRMADA (aprovação do CÉREBRO, 30/09 -- URL real testada:
    tiktok.com/@cazetv/video/7659614093048909064): a primeira tentativa desta otimização usava
    "vcodec^=avc" (nomenclatura do YouTube), mas o yt-dlp -F real do TikTok reporta o H.264
    literalmente como "h264" ("h264_540p... h264 aac") -- o filtro nunca casava com nenhum format
    do TikTok, e o fallback sem filtro escolhia o "best" entre TODOS os formats, incluindo um
    "bytevc1_1080p..." reportado como vcodec="h265"/HEVC -- resultando no arquivo real baixado
    ser 1080x1918 HEVC+AAC, nunca elegível para `-c:v copy`, e forçando transcode completo
    (~178-233s).

    O filtro agora usa "h264" (a nomenclatura REAL que o TikTok reporta) primeiro, com "avc"
    mantido como alternativa defensiva (nomenclatura do YouTube, sem custo quando "h264" já casa
    primeiro). O ÚLTIMO elo do fallback continua sendo, literalmente, o format ANTIGO e inteiro
    ("mp4/best[ext=mp4]/best"), preservado por completo: nenhum vídeo sem format H.264 disponível
    baixa diferente do que já baixava, e nenhuma resolução/qualidade é reduzida propositalmente
    ("best" sem filtro de altura escolhe o MELHOR H.264 disponível, nunca um pior)."""
    from download.ytdlp_downloader import YtDlpDownloader

    spec = service_module._SPECS[Platform.TIKTOK]
    assert spec.extra_opts.get("format") == _FORMAT_TIKTOK_ESPERADO

    options = YtDlpDownloader(spec)._build_options(Path("/tmp/stub"))
    assert options["format"] == _FORMAT_TIKTOK_ESPERADO
    # o fallback ANTIGO ("mp4/best[ext=mp4]/best") continua, literal e por inteiro, como sufixo
    assert options["format"].endswith("/mp4/best[ext=mp4]/best")
    primeiro_elo = options["format"].split("/", 1)[0]
    # a nomenclatura REAL do TikTok ("h264") vem PRIMEIRO -- é o que corrige a causa raiz
    assert primeiro_elo == "best[vcodec^=h264][acodec^=mp4a]"


def test_tiktok_nao_volta_a_escolher_hevc_quando_h264_esta_disponivel():
    """Proteção de regressão direta contra a causa raiz: nenhum elo do seletor do TikTok, exceto
    o ÚLTIMO fallback sem filtro (idêntico ao comportamento anterior a esta correção), pode casar
    com um format HEVC/bytevc1 -- os quatro primeiros elos filtram explicitamente por
    vcodec^=h264 ou vcodec^=avc, nunca deixando um HEVC "vencer" enquanto H.264 estiver disponível."""
    spec = service_module._SPECS[Platform.TIKTOK]
    elos = spec.extra_opts["format"].split("/")
    # os 2 primeiros elos (antes do "mp4/best[ext=mp4]/best" final, que tem seu próprio "/") filtram H.264
    elos_com_filtro_de_codec = [e for e in elos if "vcodec" in e]
    assert len(elos_com_filtro_de_codec) == 4
    for elo in elos_com_filtro_de_codec:
        assert "vcodec^=h264" in elo or "vcodec^=avc" in elo


def test_tiktok_continua_com_impersonate_chrome_junto_do_novo_format():
    """O novo "format" é ADITIVO -- não pode ter removido/alterado o "impersonate": "chrome" já
    existente (necessário para resolver o desafio/challenge do TikTok antes de qualquer download)."""
    spec = service_module._SPECS[Platform.TIKTOK]
    assert spec.extra_opts.get("impersonate") == "chrome"
    assert set(spec.extra_opts.keys()) == {"impersonate", "format"}


# ------------------------------------------------------------------ format do Pinterest (correção real, 30/09 -- causa raiz: format vídeo-only escolhido sem áudio)
_FORMAT_PINTEREST_ESPERADO = (
    "bestvideo[vcodec^=avc]+bestaudio"
    "/bestvideo[vcodec^=h264]+bestaudio"
    "/bestvideo+bestaudio"
    "/best"
)


def test_pinterest_busca_video_mais_audio_e_prefere_h264():
    """CAUSA RAIZ CONFIRMADA (aprovação do CÉREBRO, 30/09 -- URL real testada: pin.it/6i84tmn2E):
    o Pinterest serve vídeo e áudio em formats HLS SEPARADOS (yt-dlp -F real:
    "V_HLSV3_MOBILE-703" 720x1280 avc1 vídeo-only + "V_HLSV3_MOBILE-audio1-1" áudio-only) -- o
    format compartilhado antigo ("mp4/best[ext=mp4]/best") não pede nenhum merge, então
    "best[ext=mp4]" casava com o vídeo-only sozinho (seu container reportado já é mp4) e NUNCA
    considerava se havia áudio -- resultado real confirmado: MP4 H.264 720x1280 13.56s SEM
    NENHUMA faixa de áudio.

    O novo format usa "bestvideo+bestaudio" -- que EXIGE um stream de vídeo E um de áudio,
    nunca aceita implicitamente um vídeo-only como se fosse completo -- preferindo primeiro o
    melhor vídeo em H.264/AVC (nomenclatura "avc1", confirmada pela evidência real desta
    plataforma), com "h264" como alternativa defensiva."""
    from download.ytdlp_downloader import YtDlpDownloader

    spec = service_module._SPECS[Platform.PINTEREST]
    assert spec.extra_opts.get("format") == _FORMAT_PINTEREST_ESPERADO

    options = YtDlpDownloader(spec)._build_options(Path("/tmp/stub"))
    assert options["format"] == _FORMAT_PINTEREST_ESPERADO
    assert options["merge_output_format"] == "mp4"
    primeiro_elo = options["format"].split("/", 1)[0]
    assert primeiro_elo == "bestvideo[vcodec^=avc]+bestaudio"


def test_pinterest_nao_pode_mais_entregar_video_sem_audio():
    """Proteção de regressão direta contra a causa raiz: NENHUM elo do seletor do Pinterest pode
    resolver para um format vídeo-only sozinho (sem "+bestaudio"), exceto o ÚLTIMO recurso
    absoluto ("best", só para o caso raro de não existir nenhum par vídeo+áudio separável) --
    isso é o que impede a repetição do bug real (vídeo H.264 720x1280 baixado SEM áudio)."""
    spec = service_module._SPECS[Platform.PINTEREST]
    elos = spec.extra_opts["format"].split("/")
    # todo elo que menciona "bestvideo" tem que vir acompanhado de "+bestaudio" no MESMO elo
    for elo in elos:
        if "bestvideo" in elo:
            assert "+bestaudio" in elo, elo
    assert elos[-1] == "best"  # último recurso, sem filtro -- nunca o primeiro nem o único


def test_pinterest_selector_nao_hardcoda_ids_especificos_do_pin_de_teste():
    """O selector deve ser GENÉRICO -- nunca os format_id literais de um pin específico
    (aprovação do CÉREBRO: "NÃO hardcodar os IDs V_HLSV3_MOBILE-703 ou
    V_HLSV3_MOBILE-audio1-1 -- esses IDs são específicos desse pin")."""
    spec = service_module._SPECS[Platform.PINTEREST]
    formato = spec.extra_opts["format"]
    assert "V_HLSV3_MOBILE" not in formato
    assert "703" not in formato and "audio1-1" not in formato


def test_pinterest_continua_sem_impersonate_e_sem_extractor_args():
    """O novo "format" é o ÚNICO extra_opt do Pinterest -- não ganhou nenhum outro campo (o
    Pinterest nunca precisou de impersonate, diferente do TikTok)."""
    spec = service_module._SPECS[Platform.PINTEREST]
    assert set(spec.extra_opts.keys()) == {"format"}


def test_youtube_allowed_extractors_continua_intacto_e_extra_opts_tem_so_format():
    """Confirma que, depois da remoção do player_client forçado, a spec do YouTube ficou só com
    "format" -- allowed_extractors (segurança contra o extractor genérico) continua intacto, e
    não sobrou nenhum resquício de extractor_args/mweb."""
    spec = service_module._SPECS[Platform.YOUTUBE]
    assert spec.allowed_extractors == ("Youtube",)
    assert set(spec.extra_opts.keys()) == {"format"}


def test_configuracao_do_youtube_nao_vazou_para_outras_plataformas():
    """TikTok/Instagram/Pinterest não devem ter ganhado "format" nem qualquer resquício de
    "extractor_args"/"player_client" por causa das mudanças feitas na spec do YouTube."""
    from download.ytdlp_downloader import YtDlpDownloader

    for plataforma in (Platform.TIKTOK, Platform.INSTAGRAM, Platform.PINTEREST):
        spec = service_module._SPECS[plataforma]
        assert "extractor_args" not in spec.extra_opts, plataforma
        options = YtDlpDownloader(spec)._build_options(Path("/tmp/stub"))
        assert "extractor_args" not in options, plataforma


# ------------------------------------------------------------------ regressão: YouTube congelado (homologado, 30/09 -- gerações 47/48/53, modo=copy, HTTP 200)
def test_regressao_youtube_continua_congelado_apos_correcao_de_tiktok_e_pinterest():
    """YouTube foi homologado e CONGELADO (aprovação do CÉREBRO, 30/09 -- gerações 47/48/53
    reais, modo=copy, concluídas e baixadas com HTTP 200). Esta rodada corrigiu TikTok e
    Pinterest -- este teste prova que a spec do YouTube continua BIT A BIT idêntica à validada, e
    que NENHUM dos dois novos formats (TikTok ou Pinterest) vazou para ela (specs são entradas
    independentes do mesmo dict, mas um erro de cópia/referência acidental já causou bugs assim
    em outros projetos)."""
    spec_youtube = service_module._SPECS[Platform.YOUTUBE]
    assert spec_youtube.extra_opts == {"format": _FORMAT_YOUTUBE_ESPERADO}
    assert spec_youtube.allowed_extractors == ("Youtube",)
    # o format do YouTube nunca pode ter ganhado nenhum dos padrões de fallback do TikTok/Pinterest
    assert "mp4/best[ext=mp4]/best" not in spec_youtube.extra_opts["format"]
    assert "vcodec^=h264" not in spec_youtube.extra_opts["format"]  # nomenclatura do TikTok, não do YouTube
    # o elo do YouTube sempre filtra o ÁUDIO também (acodec^=mp4a) -- diferente do elo equivalente
    # do Pinterest ("bestvideo[vcodec^=avc]+bestaudio", sem filtro de acodec)
    assert "bestvideo[vcodec^=avc]+bestaudio[acodec^=mp4a]" in spec_youtube.extra_opts["format"]


def test_regressao_processor_continua_identico_apos_correcao_de_tiktok_e_pinterest():
    """A decisão de 3 vias do processor (copy / copy_video_transcode_audio / transcode) é
    GENÉRICA e não foi tocada nesta rodada -- confirma que as constantes de codec seguro
    continuam exatamente as mesmas que já produziam `modo=copy` para o YouTube (gerações
    47/48/53)."""
    from processor.service import _COPY_SAFE_AUDIO_CODECS, _COPY_SAFE_VIDEO_CODECS

    assert _COPY_SAFE_VIDEO_CODECS == frozenset({"h264"})
    assert _COPY_SAFE_AUDIO_CODECS == frozenset({"aac"})


# ------------------------------------------------------------------ timeout
def test_timeout_interrompe_a_espera_do_chamador(registry, monkeypatch):
    _sem_ssrf(monkeypatch)
    monkeypatch.setattr("download.service.DOWNLOAD_TIMEOUT_SECONDS", 0.1)
    registry[Platform.TIKTOK].delay = 2.0
    inicio = time.monotonic()
    with pytest.raises(DownloadTimeoutError):
        download_video("https://www.tiktok.com/@a/video/1")
    assert time.monotonic() - inicio < 1.0  # não esperou os 2s do downloader
