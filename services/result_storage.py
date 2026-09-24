"""Storage temporário do MP4 final (Etapa 8B.2): guarda o resultado já processado até que uma
etapa futura (8B.3) o entregue ao usuário, ou uma etapa futura (8B.4) o expire por TTL.

Interface mínima de propósito (save/exists/delete): nada de load()/open()/stream()/resolve_path()
aqui — isso só seria necessário para servir o download (8B.3), fora do escopo desta etapa.

Chave (`output_storage_key`): um uuid4().hex NOVO, gerado aqui — nunca reaproveita o nome do
stub que o processor/ usou internamente, para manter o storage desacoplado de um detalhe de
implementação da camada de processamento. O caminho físico (RESULT_STORAGE_DIR/<key>.mp4) nunca
sai deste módulo: quem chama só conhece a chave, nunca um caminho — e a chave nunca é aceita como
entrada externa (não vem do usuário, é sempre gerada aqui).

save() usa Path.rename(): é uma operação de METADADOS do filesystem, não lê nem copia os bytes do
vídeo (sem pico de RAM, sem arquivo duplicado em disco) — por isso RESULT_STORAGE_DIR precisa
ficar no mesmo volume que PROCESSING_TEMP_DIR (ver comentário em config.py).
"""
import logging
import re
import uuid
from pathlib import Path

from config import RESULT_STORAGE_DIR

logger = logging.getLogger("minhoca")

# Toda chave gerada por save() é um uuid4().hex — 32 caracteres hexadecimais em minúsculas. Exigir
# esse formato em _path_for() é a defesa contra uma storage_key malformada/maliciosa (ex.: contendo
# "../") escapar de RESULT_STORAGE_DIR: mesmo que nada nesta etapa chame exists()/delete() com uma
# chave vinda de fora, essa validação garante que a resolução chave -> caminho NUNCA sai do
# diretório de storage, qualquer que seja a string recebida.
_KEY_RE = re.compile(r"^[0-9a-f]{32}$")


def _ensure_dir() -> Path:
    directory = Path(RESULT_STORAGE_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _path_for(storage_key: str) -> Path:
    """Única função que conhece a relação chave -> caminho físico. `storage_key` nunca é aceito
    de fora deste módulo como dado externo — é sempre uma chave já gerada por `save()`. Levanta
    ValueError para qualquer string que não tenha o formato exato de uma chave gerada aqui —
    nunca resolve para um caminho fora de RESULT_STORAGE_DIR."""
    if not _KEY_RE.fullmatch(storage_key):
        raise ValueError(f"storage_key com formato inválido: {storage_key!r}")
    return _ensure_dir() / f"{storage_key}.mp4"


def save(source_path: Path, *, size_bytes: int) -> str:
    """Move `source_path` (o MP4 já validado pelo processor/) para dentro do storage. Devolve o
    `output_storage_key` gerado. `size_bytes` não é usado para nada além de log — o tamanho real
    já foi calculado por processor/service.py; este parâmetro só existe para deixar explícito, no
    ponto de chamada, que o tamanho já é conhecido antes do save (não é recalculado aqui)."""
    key = uuid.uuid4().hex
    destination = _path_for(key)
    source_path.rename(destination)  # rename: sem copiar bytes, sem pico de RAM
    logger.info("[RESULT_STORAGE] salvo key=%s bytes=%s", key, size_bytes)
    return key


def exists(storage_key: str) -> bool:
    try:
        return _path_for(storage_key).is_file()
    except ValueError:
        return False


def delete(storage_key: str) -> None:
    """Remove o arquivo da chave, se existir. Nunca levanta (mesmo padrão de
    download/tempfiles.cleanup e processor/tempfiles.cleanup)."""
    try:
        _path_for(storage_key).unlink(missing_ok=True)
    except ValueError:
        logger.warning("[RESULT_STORAGE] delete() recebeu uma chave com formato inválido: %r", storage_key)
    except OSError:
        logger.warning("[RESULT_STORAGE] falha ao remover key=%s", storage_key)
