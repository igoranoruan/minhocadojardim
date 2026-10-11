"""Sanitização do `filename` opcional de um item de lote (Etapa 9.3).

Esta função valida e normaliza a entrada no momento da criação do lote
(rejeita path traversal, caracteres perigosos e nomes inválidos) e garante
a extensão `.mp4`.

O valor devolvido é o que `services.usage.reserve_batch` grava em
`Generation.display_filename` e que os downloads individual e ZIP usam
como nome de exibição.

A função NÃO rejeita nomes repetidos dentro do mesmo lote. Dois ou mais
itens podem ter o mesmo `filename`; a deduplicação necessária para evitar
sobrescrita é responsabilidade da montagem do ZIP.

08/10/2026 (pedido do CÉREBRO): antes, um acento (ã, ç, é etc.) no nome
fazia a geração inteira falhar com "Nome de arquivo contém caracteres não
permitidos.", obrigando o usuário a digitar tudo de novo sem acento. Agora
acentos/diacríticos são removidos automaticamente ANTES da validação
("não" vira "nao", "ação" vira "acao") -- ver _remover_acentos(). Só
depois disso a regra de segurança (path traversal, caracteres fora do
conjunto seguro) continua rodando exatamente como antes; um emoji, um
caractere de outro alfabeto (ex.: chinês) ou qualquer coisa que não seja
letra latina acentuada ainda é rejeitado, nunca descartado em silêncio.

11/10/2026 (extração de áudio, autorização do CÉREBRO): `sanitize_batch_filename`/
`sanitize_batch_filenames` ganharam um parâmetro OPCIONAL `suffix` (default ".mp4",
IDÊNTICO ao comportamento de antes desta mudança para todo chamador existente, que
nunca o informa) -- services/audio_flow.py chama com `suffix=".mp3"` para reaproveitar
EXATAMENTE a mesma sanitização (acentos, path traversal, caracteres permitidos) em vez
de duplicar a função só para trocar a extensão forçada.
"""

import re
import unicodedata


# Letras/dígitos ASCII, espaço, ponto, hífen e underscore.
# Caracteres como barra, barra invertida e dois-pontos são rejeitados.
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9 ._-]{1,200}$")
_MP4_SUFFIX = ".mp4"


class InvalidFilenameError(Exception):
    """`filename` inválido: vazio após normalização, path traversal
    ou caractere não permitido.
    """


def _remover_acentos(texto: str) -> str:
    """Remove acentos/diacríticos via decomposição Unicode (NFKD): separa a letra base de cada
    sinal (ex.: "ã" -> "a" + til) e descarta só os sinais (unicodedata.combining != 0) -- "não"
    vira "nao", "ação" vira "acao", "Olá" vira "Ola". Letras sem diacrítico e qualquer caractere
    fora do alfabeto latino (emoji, ideograma etc.) passam intactos por aqui; continuam sendo
    barrados depois por _SAFE_NAME_RE, exatamente como antes desta mudança."""
    decomposto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in decomposto if not unicodedata.combining(c))


def sanitize_batch_filename(raw: str | None, *, suffix: str = _MP4_SUFFIX) -> str | None:
    """Valida e normaliza um filename opcional.

    `None` ou string vazia após normalização retornam `None`.

    Acentos/diacríticos são removidos automaticamente (ver _remover_acentos) antes de qualquer
    validação -- um nome com acento nunca mais é rejeitado só por causa do acento.

    Nomes válidos recebem `suffix` quando necessário (default ".mp4" -- mesmo comportamento de
    sempre; a extração de áudio passa `suffix=".mp3"`, ver docstring do módulo).

    Tentativas de path traversal ou caracteres fora do conjunto permitido (depois de acentos já
    removidos) levantam `InvalidFilenameError`.
    """
    if raw is None:
        return None

    nome = raw.strip()

    if not nome:
        return None

    nome = _remover_acentos(nome)

    if not nome:
        return None

    if "/" in nome or "\\" in nome or ".." in nome or re.match(r"^[A-Za-z]:", nome):
        raise InvalidFilenameError("Nome de arquivo inválido.")

    if not _SAFE_NAME_RE.match(nome):
        raise InvalidFilenameError(
            "Nome de arquivo contém caracteres não permitidos."
        )

    if not nome.lower().endswith(suffix):
        nome = f"{nome.rstrip('.')}{suffix}"

        # Nome formado apenas por pontos não pode virar somente o sufixo (ex.: ".mp4"/".mp3").
        if nome == suffix:
            raise InvalidFilenameError("Nome de arquivo inválido.")

    return nome


def sanitize_batch_filenames(
    raw_filenames: list[str | None], *, suffix: str = _MP4_SUFFIX,
) -> list[str | None]:
    """Sanitiza todos os filenames na ordem recebida.

    Nomes repetidos são permitidos. A função apenas valida e normaliza cada
    item individualmente.

    A deduplicação para nomes repetidos no ZIP é feita posteriormente pela
    camada responsável pela criação do arquivo ZIP.

    `suffix` (default ".mp4") é repassado para cada chamada de
    `sanitize_batch_filename` -- ver aquela função.
    """
    return [sanitize_batch_filename(raw, suffix=suffix) for raw in raw_filenames]