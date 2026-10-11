"""Detecção de links de PÁGINA DE ÁUDIO (TikTok `/music/...`, Instagram `/reels/audio/...` e
`/audio/...`) -- Parte 1 da extração de áudio, autorização do CÉREBRO (11/10/2026).

Por que isto existe: esses links NÃO são de um vídeo/post individual -- são a página do SOM/
música em si (TikTok) ou a página de áudio do Instagram. A investigação feita nesta mesma etapa
(ver o relatório final) confirmou que nenhuma das duas plataformas tem, hoje, um caminho CONFIÁVEL
e sem login para extrair o ÁUDIO ORIGINAL a partir desses links (TikTok: o yt-dlp até tem um
extrator para `/music/...` -- TikTokSoundIE --, mas está marcado `_WORKING = False` pelos próprios
mantenedores do yt-dlp, e mesmo funcionando só devolveria a LISTA de vídeos que usam o som, nunca
o áudio original completo; Instagram: o yt-dlp não tem NENHUM extrator para `/reels/audio/...` --
o regex de InstagramIE exclui esse caminho explicitamente). Por pedido EXATO do CÉREBRO, em vez de
deixar esses links caírem no fluxo normal (que falharia de um jeito confuso -- "plataforma não
suportada" ou um erro genérico do yt-dlp), detectamos ANTES de qualquer tentativa de download e
respondemos com uma mensagem amigável, SEM consumir nenhuma extração do limite diário.

Detecção é só sobre a URL COMO FOI COLADA (host + caminho), nunca rede/DNS -- mesma filosofia (e
mesmo nível de confiança) de download/platform.py::detect_platform. LIMITAÇÃO CONHECIDA E
DELIBERADA: um link ENCURTADO que REDIRECIONE para uma página de áudio (ex.: um vm.tiktok.com que
aponte para `/music/...`) não é pego aqui -- isso exigiria resolver o redirecionamento (rede) antes
de decidir qualquer coisa, o que vai contra o próprio objetivo desta checagem ("antes de tentar
baixar", sem gastar uma chamada de rede sequer). Esse caso raro segue para o fluxo normal e falha
do jeito que já falhava antes desta etapa -- documentado no relatório, não é uma falha de
segurança, só uma mensagem menos amigável nesse caso específico.
"""
from urllib.parse import urlsplit

from download.errors import DownloadError

# Texto EXATO pedido pelo CÉREBRO -- nunca reescrito/resumido.
AUDIO_PAGE_LINK_MESSAGE = (
    "Esse é o link da página do áudio. Abra um dos vídeos que usam esse som, "
    "toque em Compartilhar → Copiar link e cole aqui."
)

# Mesmos hosts de TikTok/Instagram já reconhecidos por download/platform.py, exceto os
# encurtadores (vm./vt.tiktok.com, pin.it não se aplica aqui) -- um link encurtado nunca carrega o
# caminho real "/music/.../reels/audio/..." literalmente na URL colada (ver limitação no
# docstring do módulo).
_TIKTOK_HOSTS = frozenset({"tiktok.com", "www.tiktok.com", "m.tiktok.com"})
_INSTAGRAM_HOSTS = frozenset({"instagram.com", "www.instagram.com"})


class AudioPageLinkError(DownloadError):
    """Levantado quando a URL é uma página de SOM/áudio, não um vídeo/post individual -- handler
    HTTP já existente (DownloadError é registrado globalmente em main.py, ver
    routes/generations.py::download_error_handler) traduz isto para 422 com `user_message` exata,
    sem precisar de nenhum handler novo. Nunca levantado depois de qualquer reserva/download --
    sempre a PRIMEIRA checagem do fluxo (ver services/audio_flow.py)."""

    user_message = AUDIO_PAGE_LINK_MESSAGE


def is_audio_page_link(url) -> bool:
    """True quando `url` é uma página de SOM/áudio (TikTok `/music/...`) ou de áudio do Instagram
    (`/reels/audio/...` ou `/audio/...`). Nunca levanta -- uma URL malformada/vazia simplesmente
    devolve False (quem chama já vai validar a URL de verdade logo em seguida, com
    download/url_safety.py::validate_url)."""
    if not isinstance(url, str) or not url.strip():
        return False
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    path = parts.path or ""
    if host in _TIKTOK_HOSTS:
        return path.startswith("/music/")
    if host in _INSTAGRAM_HOSTS:
        return path.startswith("/reels/audio/") or path.startswith("/audio/")
    return False


def partition_audio_batch_items(
    items: list[dict],
) -> tuple[list[tuple[int, dict]], list[int], list[dict]]:
    """Separa os itens de um LOTE de áudio em dois grupos, preservando a ORDEM/posição original (1-
    indexada, a ordem em que o usuário colou os links no frontend):

    - `pulados`: lista de (posição_original, item) cuja URL é uma página de áudio (is_audio_page_link).
      Nunca vão para reserve_audio_batch -- nunca consomem o limite diário nem contam no tamanho do
      lote.
    - `posicoes_originais`/`itens_reais`: os DEMAIS itens, na mesma ordem relativa entre si, com
      `posicoes_originais[i]` sendo a posição original (1-indexada) de `itens_reais[i]` -- usado
      para traduzir de volta a posição 1..M (M = len(itens_reais), a numeração interna que
      reserve_audio_batch/run_audio_batch usam) para a posição que o usuário realmente viu no
      formulário, nos eventos SSE (ver routes/audio.py::_run_audio_batch_in_background).

    Função pura (sem banco, sem rede) -- só reorganiza a lista `items` (cada um um dict
    {"url", "filename"}, mesmo formato que routes/audio.py já monta a partir do payload)."""
    pulados: list[tuple[int, dict]] = []
    posicoes_originais: list[int] = []
    itens_reais: list[dict] = []
    for posicao_original, item in enumerate(items, start=1):
        if is_audio_page_link(item["url"]):
            pulados.append((posicao_original, item))
        else:
            posicoes_originais.append(posicao_original)
            itens_reais.append(item)
    return pulados, posicoes_originais, itens_reais
