"""Interface que toda plataforma implementa. download/service.py é o único lugar que decide QUAL
implementação usar para cada Platform — o resto do sistema só conhece DownloadResult.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from download.platform import Platform


@dataclass(frozen=True)
class RawDownload:
    """Saída crua de um PlatformDownloader, antes da validação final (file_validation.py)."""

    path: Path
    duration_seconds: float | None


class PlatformDownloader(ABC):
    platform: Platform

    @abstractmethod
    def download(self, url: str, dest_stub: Path) -> RawDownload:
        """Baixa `url` para um arquivo cujo nome começa com `dest_stub` (a extensão final é
        decidida pela implementação). Levanta DownloadError (ou subclasse) em qualquer falha."""
