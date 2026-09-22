"""Resultado padronizado do processamento."""
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProcessingResult:
    """MP4 final, já validado. output_sha256 só existe DEPOIS da validação final do arquivo de
    saída (nunca é calculado sobre um arquivo que ainda pode ser rejeitado)."""

    output_path: Path
    size_bytes: int
    duration_seconds: float
    has_audio: bool
    output_sha256: str
