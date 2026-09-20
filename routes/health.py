"""Health check. Usado por você (teste local) e pelo Render (monitoramento).

Etapa 1: não toca em banco nem em serviços externos.
Na Etapa 2 ele passa a verificar também o banco de dados.
"""
from fastapi import APIRouter

from config import APP_NAME, APP_VERSION, settings
from utils.time_sp import now_sp

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
