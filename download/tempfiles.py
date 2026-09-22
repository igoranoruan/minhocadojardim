"""Arquivo temporário: nome seguro (nunca derivado de dado externo) e limpeza garantida em erro.

"Stub" é o caminho SEM extensão — a extensão final é decidida pelo downloader (ex.: .mp4). Em
sucesso, o arquivo final é devolvido ao chamador (Etapa 6), que passa a ser dono dele e decide
quando apagar. Em qualquer erro, `cleanup` remove TODO arquivo cujo nome comece pelo stub
(inclusive .part/.ytdl de um download incompleto).
"""
import uuid
from pathlib import Path

from config import DOWNLOAD_TEMP_DIR


def _ensure_dir() -> Path:
    directory = Path(DOWNLOAD_TEMP_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def new_temp_stub() -> Path:
    """Caminho SEM extensão, com nome aleatório (uuid4) — nunca derivado do nome da plataforma,
    do título do vídeo ou de qualquer outro dado vindo da URL/do usuário."""
    return _ensure_dir() / uuid.uuid4().hex


def cleanup(stub: Path) -> int:
    """Remove todo arquivo `stub.*` (o final e quaisquer .part/.ytdl deixados por uma falha).
    Devolve quantos arquivos foram removidos. Nunca levanta exceção."""
    removed = 0
    for path in stub.parent.glob(f"{stub.name}.*"):
        try:
            path.unlink()
            removed += 1
        except OSError:
            pass
    return removed
