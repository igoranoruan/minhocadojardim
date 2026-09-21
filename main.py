"""Ponto de entrada do Minhoca de Jardim.

Este arquivo só monta a aplicação: logs, rotas e frontend.
Regras de negócio ficam em services/, downloads em download/, FFmpeg em processor/.
"""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from config import APP_NAME, APP_VERSION, settings
from database.session import dispose_engine
from routes import auth, health
from services.auth import AuthError
from utils.logging_setup import setup_logging
from utils.origin import OriginProtectionMiddleware

setup_logging(settings.log_level)
logger = logging.getLogger("minhoca")

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(
        "[STARTUP] %s v%s | env=%s | public_base_url=%s",
        APP_NAME,
        APP_VERSION,
        settings.env,
        settings.public_base_url,
    )
    if settings.using_dev_secrets:
        logger.warning("[STARTUP] usando segredos de DESENVOLVIMENTO (AUTH_SECRET_KEY/IP_HASH_SECRET não definidos)")
    yield
    dispose_engine()
    logger.info("[SHUTDOWN] %s encerrado", APP_NAME)


app = FastAPI(
    title=APP_NAME,
    version=APP_VERSION,
    lifespan=lifespan,
    # Documentação automática só em desenvolvimento.
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None,
    openapi_url=None if settings.is_production else "/openapi.json",
)

# Proteção de Origin (CSRF) centralizada: vale para toda requisição que altera dados.
app.add_middleware(OriginProtectionMiddleware)

# Erros de autenticação e de corpo inválido nas rotas /api/auth: {"detail": "...", "code": "..."}.
app.add_exception_handler(AuthError, auth.auth_error_handler)
app.add_exception_handler(RequestValidationError, auth.validation_error_handler)

# Rotas da API.
app.include_router(health.router)
app.include_router(auth.router)


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    """Página principal do Minhoca."""
    return FileResponse(STATIC_DIR / "index.html")


# Arquivos do frontend (imagens, favicon etc.), acessados em /static/...
# O index.html aprovado já referencia /static/favicon.png.
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
