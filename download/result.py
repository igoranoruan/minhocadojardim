"""Resultado padronizado do download (mesmo formato para as 4 plataformas)."""
from dataclasses import dataclass
from pathlib import Path

from download.platform import Platform


@dataclass(frozen=True)
class DownloadResult:
    """Arquivo temporário validado, pronto para a Etapa 6 (processamento).

    duration_seconds: float | None
        Só vem preenchida quando a PLATAFORMA informa a duração de forma confiável antes ou
        durante a extração (ex.: metadados do YouTube). Quando ausente, fica None — a validação
        DEFINITIVA de duração (ffprobe) é responsabilidade da Etapa 6. Ver download/errors.py e
        MAX_VIDEO_DURATION_SECONDS em config.py para a checagem antecipada, quando disponível.
    """

    platform: Platform
    temp_path: Path
    size_bytes: int
    duration_seconds: float | None
    container_format: str
    # "video" | "image" (03/10/2026 -- suporte a Pinterest/Instagram sem vídeo, aprovação do
    # CÉREBRO). Default "video" preserva, sem nenhuma mudança, todo código/teste existente que já
    # constrói um DownloadResult sem conhecer este campo -- só download/service.py (o único lugar
    # que decide isto de verdade, via download.file_validation.detect_media_type) passa "image"
    # explicitamente.
    media_type: str = "video"
