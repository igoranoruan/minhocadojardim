"""Utilitários dos testes da Etapa 7 (não são testes nem fixtures)."""
import secrets
from datetime import timedelta
from pathlib import Path

from database.models import AuthSession, User
from database.types import utcnow
from download.platform import Platform
from download.result import DownloadResult
from processor.result import ProcessingResult
from services.auth import hash_session_token


def login_directly(client, session, user: User, *, cookie_name: str = "minhoca_session") -> None:
    """Autentica `client` como `user` sem passar pelo fluxo de código por e-mail (já testado na
    Etapa 3): cria uma AuthSession de verdade e planta o cookie correspondente no TestClient.
    Usa services.auth.hash_session_token, a MESMA função que routes/deps.get_current_user usa
    para validar a sessão — não reimplementa nada."""
    token = secrets.token_urlsafe(32)
    now = utcnow()
    session.add(
        AuthSession(
            user_id=user.id,
            token_hash=hash_session_token(token),
            created_at=now,
            last_used_at=now,
            expires_at=now + timedelta(days=30),
        )
    )
    session.commit()
    client.cookies.set(cookie_name, token)


def fake_download_result(temp_path: Path, *, platform: Platform = Platform.TIKTOK, size=999, duration=10.0) -> DownloadResult:
    return DownloadResult(
        platform=platform, temp_path=temp_path, size_bytes=size,
        duration_seconds=duration, container_format="mp4",
    )


def fake_processing_result(output_path: Path, *, sha="a" * 64, duration=12.3, size=1234, has_audio=True) -> ProcessingResult:
    return ProcessingResult(
        output_path=output_path, size_bytes=size, duration_seconds=duration,
        has_audio=has_audio, output_sha256=sha,
    )
