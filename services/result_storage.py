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
import time
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

# 03/10/2026 (suporte a imagem, aprovação do CÉREBRO): este módulo é propositalmente ISOLADO (ver
# test_result_storage_nao_importa_banco_rotas_processor_download_ou_fastapi) -- por isso a lista de
# extensões permitidas é definida AQUI, duplicada de download/file_validation.py em vez de
# importada de lá. `extension` nunca vem direto do usuário: é sempre o valor já validado pela
# camada de download/processamento (download.result.DownloadResult.media_type ->
# database.models.generation.Generation.output_extension), mas ainda assim passa por esta mesma
# validação de allow-list antes de virar parte de um caminho de arquivo -- mesma defesa que já
# existia para storage_key, agora também para extension.
_ALLOWED_EXTENSIONS = frozenset(
    {"mp4", "mkv", "webm", "mov", "m4v", "jpg", "jpeg", "png", "webp", "zip", "mp3"}
    # "mp3" (11/10/2026, extração de áudio -- autorização do CÉREBRO): services/audio_flow.py salva
    # o MP3 final aqui com extension="mp3", mesmo padrão já usado para "jpg" (imagem).
)


def _ensure_dir() -> Path:
    directory = Path(RESULT_STORAGE_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _path_for(storage_key: str, extension: str = "mp4") -> Path:
    """Única função que conhece a relação chave -> caminho físico. `storage_key` nunca é aceito
    de fora deste módulo como dado externo — é sempre uma chave já gerada por `save()`. Levanta
    ValueError para qualquer string que não tenha o formato exato de uma chave gerada aqui, ou
    para qualquer `extension` fora de _ALLOWED_EXTENSIONS — nunca resolve para um caminho fora de
    RESULT_STORAGE_DIR. `extension` default "mp4" preserva, sem nenhuma mudança, todo chamador
    existente (vídeo) que não conhece este parâmetro."""
    if not _KEY_RE.fullmatch(storage_key):
        raise ValueError(f"storage_key com formato inválido: {storage_key!r}")
    if extension not in _ALLOWED_EXTENSIONS:
        raise ValueError(f"extensão não permitida: {extension!r}")
    return _ensure_dir() / f"{storage_key}.{extension}"


def save(source_path: Path, *, size_bytes: int, extension: str = "mp4") -> str:
    """Move `source_path` (o resultado já validado pelo processor/) para dentro do storage.
    Devolve o `output_storage_key` gerado. `size_bytes` não é usado para nada além de log — o
    tamanho real já foi calculado por processor/service.py; este parâmetro só existe para deixar
    explícito, no ponto de chamada, que o tamanho já é conhecido antes do save (não é recalculado
    aqui). `extension` (03/10/2026, suporte a imagem): default "mp4" preserva o comportamento
    exato de antes desta mudança para todo chamador de vídeo; quem salva uma imagem passa a
    extensão real (ex.: "jpg")."""
    key = uuid.uuid4().hex
    destination = _path_for(key, extension)
    source_path.rename(destination)  # rename: sem copiar bytes, sem pico de RAM
    logger.info("[RESULT_STORAGE] salvo key=%s bytes=%s extensao=%s", key, size_bytes, extension)
    return key


def resolve_path(storage_key: str, extension: str = "mp4") -> Path:
    """Etapa 8B.3: única forma pública de obter o Path físico de um resultado salvo — usada só
    pela rota de download, DEPOIS de já ter confirmado (services.usage.get_downloadable_generation
    + exists()) que a geração é do usuário certo, está completed, não expirou e o arquivo existe.
    Reaproveita a MESMA validação de _path_for(): uma storage_key fora do formato uuid4().hex, ou
    uma extension fora da allow-list, nunca chega a virar caminho — levanta ValueError, nunca
    devolve um caminho fora de RESULT_STORAGE_DIR nem aceita um caminho absoluto ou vindo de
    fora."""
    return _path_for(storage_key, extension)


def exists(storage_key: str, extension: str = "mp4") -> bool:
    try:
        return _path_for(storage_key, extension).is_file()
    except ValueError:
        return False


def delete(storage_key: str, extension: str = "mp4") -> None:
    """Remove o arquivo da chave, se existir. Nunca levanta (mesmo padrão de
    download/tempfiles.cleanup e processor/tempfiles.cleanup)."""
    try:
        _path_for(storage_key, extension).unlink(missing_ok=True)
    except ValueError:
        logger.warning("[RESULT_STORAGE] delete() recebeu uma chave ou extensão com formato inválido: "
                        "key=%r extensao=%r", storage_key, extension)
    except OSError:
        logger.warning("[RESULT_STORAGE] falha ao remover key=%s", storage_key)


def sweep_expired(max_age_seconds: float, *, now: float | None = None) -> tuple[int, int]:
    """Apaga do storage os resultados (vídeo, imagem, áudio) mais velhos que `max_age_seconds`,
    pela data de modificação do arquivo. Devolve (arquivos apagados, bytes liberados).

    Pedido e aprovação do Igor (02/11/2026) -- a "etapa 8B.4" que ficou pendente: o link de download
    expira em RESULT_TTL_SECONDS (30 min, checado no banco por services.usage), mas o ARQUIVO ficava
    no disco até o servidor reiniciar. Quem chama (main.py) passa TTL + uma folga, então nada que
    ainda pode ser baixado é apagado. A data usada é o mtime: rename() em save() preserva o mtime de
    quando o processamento terminou de escrever o arquivo (~ finished_at da geração).

    Só toca arquivos com o nome EXATO que save() gera (<32 hex>.<extensão permitida>) -- qualquer
    outro arquivo no diretório é ignorado. Nunca levanta: um arquivo que não pôde ser apagado só é
    logado (mesmo padrão de delete()). Apagar um arquivo que está sendo baixado naquele instante é
    seguro no Linux: o download em andamento continua lendo até o fim."""
    agora = time.time() if now is None else now
    directory = Path(RESULT_STORAGE_DIR)
    if not directory.is_dir():
        return 0, 0
    apagados = 0
    liberados = 0
    for path in directory.iterdir():
        stem, ponto, extensao = path.name.rpartition(".")
        if not ponto or not _KEY_RE.fullmatch(stem) or extensao not in _ALLOWED_EXTENSIONS:
            continue
        try:
            info = path.stat()
            if not path.is_file() or agora - info.st_mtime <= max_age_seconds:
                continue
            path.unlink()
            apagados += 1
            liberados += info.st_size
        except FileNotFoundError:
            continue  # já removido por outra varredura/delete() -- nada a fazer
        except OSError:
            logger.warning("[RESULT_STORAGE] falha ao apagar resultado vencido %s", path.name, exc_info=True)
    return apagados, liberados

