"""Rota da geração individual: POST /api/generations.

A rota fica FINA de propósito: só resolve o usuário autenticado, chama
services.generation_flow.generate_from_url e traduz o resultado (ou a exceção) para HTTP. Toda a
regra de negócio mora em services/generation_flow.py, que não sabe nada de HTTP.

Erros: {"detail": "...", "code": "..."}, mesmo formato já usado em routes/auth.py. Nenhuma classe
de erro das Etapas 3-6 foi alterada para caber aqui — a tradução acontece só nos handlers abaixo.
"""
import logging
import re

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database.models import User
from database.session import get_session
from download.errors import DownloadError
from processor.errors import ProcessorError
from routes.deps import get_current_user
from services.entitlements import EntitlementInconsistencyError
from services.generation_flow import GenerationPersistenceError, generate_from_url
from services.usage import QuotaExceededError, UsageError

logger = logging.getLogger("minhoca")

router = APIRouter(prefix="/api", tags=["generations"])

NO_STORE = {"Cache-Control": "no-store"}
_CAMEL_CASE_RE = re.compile(r"(?<!^)(?=[A-Z])")


class CreateGenerationBody(BaseModel):
    url: str = Field(min_length=1, max_length=2048)


def _code_for(exc: BaseException) -> str:
    """'QuotaExceededError' -> 'quota_exceeded' (mesmo estilo de código já usado em auth/usage)."""
    name = exc.__class__.__name__
    if name.endswith("Error"):
        name = name[: -len("Error")]
    return _CAMEL_CASE_RE.sub("_", name).lower()


def _error_response(status_code: int, exc: BaseException, *, detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"detail": detail, "code": _code_for(exc)},
        headers=NO_STORE,
    )


async def download_error_handler(request, exc: DownloadError) -> JSONResponse:
    """URL inválida, plataforma não suportada, SSRF bloqueado, download falhou etc. — em todos os
    casos a mensagem já vem pronta e segura em exc.user_message (nunca a URL/detalhe técnico)."""
    return _error_response(422, exc, detail=exc.user_message)


async def processor_error_handler(request, exc: ProcessorError) -> JSONResponse:
    return _error_response(422, exc, detail=exc.user_message)


async def quota_exceeded_handler(request, exc: QuotaExceededError) -> JSONResponse:
    return _error_response(429, exc, detail="Você atingiu o limite de gerações do seu plano.")


async def usage_error_handler(request, exc: UsageError) -> JSONResponse:
    """Rede de segurança para outras UsageError (ex.: futuras regras) que não sejam cota
    esgotada — não deveria ocorrer nesta rota (sem lote), mas não deve virar 500 genérico."""
    return _error_response(400, exc, detail="Não foi possível processar esta geração.")


async def entitlement_inconsistency_handler(request, exc: EntitlementInconsistencyError) -> JSONResponse:
    """Sobreposição de entitlements é inconsistência de DADOS (Etapa 4), não erro do usuário:
    503, para sinalizar que é temporário/precisa de investigação, sem detalhar a causa."""
    logger.error("[GENERATION] inconsistência de entitlements: %s", exc)
    return _error_response(503, exc, detail="Não foi possível verificar seu acesso agora.")


async def generation_persistence_error_handler(request, exc: GenerationPersistenceError) -> JSONResponse:
    """Download/processamento terminaram, mas a gravação no banco falhou — problema nosso, não
    do vídeo/usuário. Nunca expõe a fase (complete/fail) nem a causa original ao cliente."""
    logger.error("[GENERATION] falha de persistência (geração %s, fase %s)", exc.generation_id, exc.phase)
    return _error_response(500, exc, detail="Não foi possível concluir o registro desta geração.")


@router.post("/generations")
def create_generation(
    body: CreateGenerationBody,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> JSONResponse:
    outcome = generate_from_url(db, user=user, url=body.url)
    return JSONResponse(
        {
            "generation_id": outcome.generation_id,
            "status": outcome.status,
            "platform": outcome.platform,
            "size_bytes": outcome.size_bytes,
            "duration_seconds": outcome.duration_seconds,
            "output_sha256": outcome.output_sha256,
        },
        headers=NO_STORE,
    )
