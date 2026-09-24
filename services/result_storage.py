"""Storage temporário do MP4 final (Etapa 8B.2): guarda o resultado já processado até que uma
etapa futura (8B.3) o entregue ao usuário, ou uma etapa futura (8B.4) o expire por TTL.

Interface mínima de propósito: save/exists/delete (Etapa 8B.2) mais resolve_path (Etapa 8B.3, só
para a rota de download ler o arquivo já validado). Nada de load()/open()/stream() aqui — quem lê
os bytes é o FileResponse do FastAPI, direto do Path devolvido por resolve_path().

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


def resolve_path(storage_key: str) -> Path:
    """Etapa 8B.3: única forma pública de obter o Path físico de um resultado salvo — usada só
    pela rota de download, DEPOIS de já ter confirmado (services.usage.get_downloadable_generation
    + exists()) que a geração é do usuário certo, está completed, não expirou e o arquivo existe.
    Reaproveita a MESMA validação de _path_for(): uma storage_key fora do formato uuid4().hex
    nunca chega a virar caminho — levanta ValueError, nunca devolve um caminho fora de
    RESULT_STORAGE_DIR nem aceita um caminho absoluto ou vindo de fora."""
    return _path_for(storage_key)


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
