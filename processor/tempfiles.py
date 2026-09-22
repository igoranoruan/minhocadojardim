"""Arquivo temporário de SAÍDA do processamento: nome seguro e limpeza garantida.

Mesmo padrão de download/tempfiles.py (Etapa 5): "stub" é o caminho sem extensão; em sucesso, o
arquivo final é devolvido ao chamador, que passa a ser dono dele; em qualquer erro, `cleanup`
remove todo arquivo `stub.*`.
"""
import uuid
from pathlib import Path

from config import PROCESSING_TEMP_DIR


def _ensure_dir() -> Path:
    directory = Path(PROCESSING_TEMP_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def new_temp_stub() -> Path:
    """Caminho SEM extensão, com nome aleatório (uuid4) — nunca derivado do nome do arquivo de
    entrada nem de qualquer metadado do vídeo."""
    return _ensure_dir() / uuid.uuid4().hex


def cleanup(stub: Path) -> int:
    """Remove todo arquivo `stub.*`. Devolve quantos foram removidos. Nunca levanta exceção."""
    removed = 0
    for path in stub.parent.glob(f"{stub.name}.*"):
        try:
            path.unlink()
            removed += 1
        except OSError:
            pass
    return removed
