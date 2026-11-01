"""@ do criador original de um vídeo, a partir do dicionário `info` que o yt-dlp já devolve no
download (ETAPA B8, pedido do Igor, 01/11/2026 -- opção "Nomear com o @ do criador").

Só LÊ campos que o yt-dlp já preenche; nenhuma chamada de rede a mais. Qual campo guarda o @ muda
por plataforma -- conferido no código-fonte do próprio yt-dlp (extractors instagram.py, tiktok.py,
youtube/_video.py e pinterest.py):

- Instagram: `channel` = username (`uploader_id` é o id numérico, `uploader` é o nome de exibição).
- TikTok: `uploader` = unique_id, o @ (`channel` é o apelido de exibição, `uploader_id` é numérico).
- YouTube: `uploader_id` = "@handle" (canais antigos podem ter um id "UC..." aqui -- sem "@", não
  é um @, então é ignorado).
- Pinterest: o yt-dlp só expõe o nome de exibição e um id numérico, nunca o username -- sempre
  devolve None (quem chama cai no nome padrão).

Devolve o @ SEM o "@" na frente, já restrito a caracteres seguros para nome de arquivo
(letras/dígitos ASCII, ponto, underscore e hífen); qualquer outra coisa (vazio, numérico puro,
caracteres fora desse conjunto, tamanho absurdo) vira None -- nunca um nome "meio certo".
"""
import re

from download.platform import Platform

# Instagram/TikTok: letras, dígitos, "." e "_"; YouTube também aceita "-". 50 já cobre com folga
# os tetos reais (Instagram 30, TikTok 24, YouTube 30).
_HANDLE_RE = re.compile(r"^[A-Za-z0-9._-]{1,50}$")

_FIELD_BY_PLATFORM: dict[Platform, str] = {
    Platform.INSTAGRAM: "channel",
    Platform.TIKTOK: "uploader",
    Platform.YOUTUBE: "uploader_id",
}


def extract_creator_handle(platform: Platform, info: object) -> str | None:
    """@ do criador (sem o "@") ou None quando a plataforma não informa um @ confiável."""
    if not isinstance(info, dict):
        return None
    field = _FIELD_BY_PLATFORM.get(platform)
    if field is None:
        return None
    raw = info.get(field)
    if not isinstance(raw, str):
        return None
    handle = raw.strip()
    if platform == Platform.YOUTUBE:
        # Só "@handle" é um @ de verdade no YouTube; "UC..." (id de canal) não serve como crédito.
        if not handle.startswith("@"):
            return None
    handle = handle.lstrip("@")
    if not _HANDLE_RE.match(handle):
        return None
    # Só dígitos é um id numérico, não um @ (proteção extra caso a plataforma mude o campo).
    if handle.isdigit():
        return None
    # Só pontos/hífens/underscores não identifica ninguém.
    if not any(c.isalnum() for c in handle):
        return None
    return handle
