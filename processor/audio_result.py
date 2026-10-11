"""Resultado padronizado da extração de áudio -- mesmo papel de processor/result.py, para MP3."""
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AudioProcessingResult:
    """MP3 final, já validado. output_sha256 só existe DEPOIS da validação final do arquivo de
    saída (nunca calculado sobre um arquivo que ainda pode ser rejeitado) -- mesma regra de
    processor/result.py::ProcessingResult."""

    output_path: Path
    size_bytes: int
    duration_seconds: float
    output_sha256: str
