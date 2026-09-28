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
from download.errors import DownloadError
from processor.errors import ProcessorError
from payments.errors import (
    InvalidPaymentMethodError,
    InvalidWebhookSignatureError,
    PaymentGatewayError,
    PaymentMethodMismatchError,
    PaymentNotFoundError,
    PlanNotPurchasableError,
    UnknownPlanError,
)
from routes import auth, generations, health, payments, webhooks
from services.auth import AuthError
from services.entitlements import EntitlementInconsistencyError
from services.generation_flow import GenerationPersistenceError
from services.usage import BatchNotFoundError, GenerationDownloadNotFoundError, QuotaExceededError, UsageError
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

# Erros da orquestração de geração (Etapa 7): tradução das camadas de download/processamento/uso
# para HTTP, sem alterar nenhuma das classes de erro originais (Etapas 3-6).
app.add_exception_handler(DownloadError, generations.download_error_handler)
app.add_exception_handler(ProcessorError, generations.processor_error_handler)
app.add_exception_handler(QuotaExceededError, generations.quota_exceeded_handler)
app.add_exception_handler(UsageError, generations.usage_error_handler)
app.add_exception_handler(EntitlementInconsistencyError, generations.entitlement_inconsistency_handler)
app.add_exception_handler(GenerationPersistenceError, generations.generation_persistence_error_handler)
app.add_exception_handler(GenerationDownloadNotFoundError, generations.generation_download_not_found_handler)
app.add_exception_handler(BatchNotFoundError, generations.batch_not_found_handler)

# Erros da fundação de pagamentos (Etapa 10.1) + checkout (Etapa 10.2): tradução para HTTP,
# sem nenhuma regra de negócio aqui -- ver payments/errors.py e routes/payments.py.
app.add_exception_handler(UnknownPlanError, payments.unknown_plan_handler)
app.add_exception_handler(PlanNotPurchasableError, payments.plan_not_purchasable_handler)
app.add_exception_handler(InvalidPaymentMethodError, payments.invalid_payment_method_handler)
app.add_exception_handler(PaymentNotFoundError, payments.payment_not_found_handler)
app.add_exception_handler(PaymentMethodMismatchError, payments.payment_method_mismatch_handler)
app.add_exception_handler(PaymentGatewayError, payments.payment_gateway_handler)

# Webhook do Mercado Pago (Etapa 10.3): handler de defesa só -- o caminho normal já trata
# InvalidWebhookSignatureError localmente dentro de routes/webhooks.py (para nunca criar
# PaymentEvent nesse caso); ver o docstring de routes/webhooks.py.
app.add_exception_handler(InvalidWebhookSignatureError, webhooks.invalid_signature_handler)

# Rotas da API.
app.include_router(health.router)
app.include_router(auth.router)
app.include_router(generations.router)
app.include_router(payments.router)
app.include_router(webhooks.router)


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    """Página principal do Minhoca."""
    return FileResponse(STATIC_DIR / "index.html")


# Arquivos do frontend (imagens, favicon etc.), acessados em /static/...
# O index.html aprovado já referencia /static/favicon.png.
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
