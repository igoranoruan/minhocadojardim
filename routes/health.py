"""Health checks. Usados por você (teste local) e pelo Render (monitoramento).

/health        -> a aplicação está de pé (não toca em banco).
/health/ready  -> a aplicação está pronta: o banco responde (200) ou não (503).
"""
import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from config import APP_NAME, APP_VERSION, settings
from database.session import get_session
from utils.time_sp import now_sp

logger = logging.getLogger("minhoca")

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "app": APP_NAME,
        "version": APP_VERSION,
        "env": settings.env,
        # Confirma que o fuso de São Paulo está funcionando (base dos limites).
        "time_sp": now_sp().isoformat(timespec="seconds"),
    }


@router.get("/health/ready", response_model=None)
def health_ready(session: Session = Depends(get_session)):
    """Verifica se o banco está acessível. Rota síncrona: o FastAPI a executa em threadpool."""
    try:
        session.execute(text("SELECT 1"))
    except Exception as exc:
        logger.error("[HEALTH] banco indisponível: %s", exc.__class__.__name__, exc_info=True)
        return JSONResponse(
            status_code=503,
            content={"status": "unavailable", "database": "unavailable"},
        )
    return {"status": "ok", "database": "ok"}
