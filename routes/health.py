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
# ----------------------------------------------------------------------------------------------
# ROTA TEMPORÁRIA DE DIAGNÓSTICO — remover depois do teste.
# Testa EXCLUSIVAMENTE conectividade de rede privada do Render entre KLANGO e BGUTIL.
# Não usa yt-dlp, não usa o plugin, não participa do pipeline de geração.
# ----------------------------------------------------------------------------------------------
@router.get("/health/bgutil-ping", response_model=None)
def health_bgutil_ping():
    import urllib.error
    import urllib.request

    from config import get_settings

    base_url = get_settings().bgutil_pot_provider_base_url
    if not base_url:
        return JSONResponse(status_code=200, content={"status": "base_url_vazia"})

    url = f"{base_url.rstrip('/')}/ping"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            corpo = resp.read().decode("utf-8", errors="replace")
            return JSONResponse(
                status_code=200,
                content={"status": "alcancado", "http_status": resp.status, "body": corpo},
            )
    except urllib.error.URLError as exc:
        return JSONResponse(
            status_code=200,
            content={"status": "falha_de_rede", "detalhe": str(exc.reason)},
        )
    except Exception as exc:
        return JSONResponse(
            status_code=200,
            content={
                "status": "erro_inesperado",
                "detalhe": f"{exc.__class__.__name__}: {exc}",
            },
        )