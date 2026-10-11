"""Utilitários dos testes de extração de áudio (não são testes nem fixtures) -- mesmo papel de
tests/helpers_generation_flow.py, para o pipeline de áudio (download/audio_service.py,
processor/audio_service.py)."""
from pathlib import Path

from download.platform import Platform
from download.result import DownloadResult
from processor.audio_result import AudioProcessingResult


def fake_audio_download_result(
    temp_path: Path, *, platform: Platform = Platform.TIKTOK, size=999, duration=10.0,
) -> DownloadResult:
    return DownloadResult(
        platform=platform, temp_path=temp_path, size_bytes=size,
        duration_seconds=duration, container_format=temp_path.suffix.lstrip("."), media_type="audio",
    )


def fake_audio_processing_result(
    output_path: Path, *, sha="a" * 64, duration=12.3, size=1234,
) -> AudioProcessingResult:
    return AudioProcessingResult(
        output_path=output_path, size_bytes=size, duration_seconds=duration, output_sha256=sha,
    )
