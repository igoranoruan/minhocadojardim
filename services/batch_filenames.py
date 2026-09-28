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
"""

import re


# Letras/dígitos ASCII, espaço, ponto, hífen e underscore.
# Caracteres como barra, barra invertida e dois-pontos são rejeitados.
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9 ._-]{1,200}$")
_MP4_SUFFIX = ".mp4"


class InvalidFilenameError(Exception):
    """`filename` inválido: vazio após normalização, path traversal
    ou caractere não permitido.
    """


def sanitize_batch_filename(raw: str | None) -> str | None:
    """Valida e normaliza um filename opcional.

    `None` ou string vazia após normalização retornam `None`.

    Nomes válidos recebem `.mp4` quando necessário.

    Tentativas de path traversal ou caracteres fora do conjunto permitido
    levantam `InvalidFilenameError`.
    """
    if raw is None:
        return None

    nome = raw.strip()

    if not nome:
        return None

    if "/" in nome or "\\" in nome or ".." in nome or re.match(r"^[A-Za-z]:", nome):
        raise InvalidFilenameError("Nome de arquivo inválido.")

    if not _SAFE_NAME_RE.match(nome):
        raise InvalidFilenameError(
            "Nome de arquivo contém caracteres não permitidos."
        )

    if not nome.lower().endswith(_MP4_SUFFIX):
        nome = f"{nome.rstrip('.')}{_MP4_SUFFIX}"

        # Nome formado apenas por pontos não pode virar somente ".mp4".
        if nome == _MP4_SUFFIX:
            raise InvalidFilenameError("Nome de arquivo inválido.")

    return nome


def sanitize_batch_filenames(
    raw_filenames: list[str | None],
) -> list[str | None]:
    """Sanitiza todos os filenames na ordem recebida.

    Nomes repetidos são permitidos. A função apenas valida e normaliza cada
    item individualmente.

    A deduplicação para nomes repetidos no ZIP é feita posteriormente pela
    camada responsável pela criação do arquivo ZIP.
    """
    return [sanitize_batch_filename(raw) for raw in raw_filenames]