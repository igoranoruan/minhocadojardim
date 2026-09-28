"""Webhook do Mercado Pago (Etapa 10.3): POST /api/webhooks/mercadopago.

Sem Depends(get_current_user) -- não é uma requisição de usuário, é servidor-a-servidor. Já
coberta pela isenção de Origin/CSRF existente desde antes desta etapa
(config.ORIGIN_CHECK_EXEMPT_PREFIXES = ("/api/webhooks/",)) -- nenhuma mudança precisou ser feita
em utils/origin.py nem em config.py para isso.

Ordem estrita (nunca invertida):
1. valida a assinatura (payments/webhook_signature.py) -- ANTES de qualquer leitura/escrita.
   Inválida -> log de aplicação + HTTP 401. NUNCA cria PaymentEvent, NUNCA consulta o Mercado
   Pago, NUNCA toca em Payment/Entitlement (write amplification -- ver payments/errors.py).
2. a partir daqui o webhook é "autenticado": cruza data.id da query com data.id do corpo (quando
   ambos existirem) -- diverge -> PaymentEvent (result="conflict"), HTTP 200, nada mais.
3. cria o PaymentEvent (o registro de auditoria de todo webhook AUTENTICADO).
4. delega a reconciliação inteira (consulta autoritativa + localização do Payment + lock +
   grant/revoke) a payments.gateway.reconcile_webhook_payment -- esta rota nunca decide sozinha
   se um pagamento foi aprovado, nunca chama grant_entitlement/revoke_entitlement diretamente.
5. falha de comunicação com o Mercado Pago (PaymentGatewayError) -> PaymentEvent
   (result="error"), HTTP 502 (permite o Mercado Pago reenviar).
6. finaliza o PaymentEvent com o resultado da reconciliação (granted/revoked/no_op/orphan/
   conflict) e responde HTTP 200 ao Mercado Pago.
"""
import json
import logging

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from config import Settings, get_settings
from database.models import PaymentEvent
from database.session import get_session
from payments.errors import InvalidWebhookSignatureError, PaymentGatewayError
from payments.gateway import reconcile_webhook_payment
from payments.webhook_signature import validate_signature
from utils.time_sp import resolve_now

logger = logging.getLogger("minhoca")

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

NO_STORE = {"Cache-Control": "no-store"}

# error só é preenchido para "conflict" e "error" -- os outros resultados são autoexplicativos.
_RESULTADOS_COM_ERROR = {"conflict", "error"}


def _create_event(db: Session, *, event_type: str | None, action: str | None, resource_id: str | None, payload_raw: str) -> PaymentEvent:
    """Cria o PaymentEvent de um webhook JÁ AUTENTICADO -- nunca chamado para assinatura inválida."""
    event = PaymentEvent(
        provider="mercadopago",
        event_type=event_type,
        action=action,
        resource_id=resource_id,
        payload=payload_raw,
    )
    db.add(event)
    db.flush()
    db.commit()
    return event


def _finish_event(db: Session, event: PaymentEvent, *, result: str, error: str | None = None) -> None:
    event.result = result
    event.error = error if result in _RESULTADOS_COM_ERROR else None
    event.processed_at = resolve_now()
    db.commit()


async def invalid_signature_handler(request: Request, exc: InvalidWebhookSignatureError) -> JSONResponse:
    """Handler global de defesa -- o caminho normal já trata InvalidWebhookSignatureError
    localmente dentro de mercadopago_webhook (para nunca criar PaymentEvent); este handler só
    existe para o caso (não esperado) de a exceção escapar de algum outro ponto."""
    logger.warning("[WEBHOOK] assinatura recusada: %s", exc)
    return JSONResponse(status_code=401, content={"detail": "Assinatura inválida.", "code": "invalid_webhook_signature"}, headers=NO_STORE)


@router.post("/mercadopago")
async def mercadopago_webhook(
    request: Request,
    data_id: str | None = Query(default=None, alias="data.id"),
    x_signature: str | None = Header(default=None),
    x_request_id: str | None = Header(default=None),
    db: Session = Depends(get_session),
    cfg: Settings = Depends(get_settings),
) -> JSONResponse:
    corpo_bruto = await request.body()
    try:
        corpo_json = json.loads(corpo_bruto) if corpo_bruto else {}
    except ValueError:
        corpo_json = {}
    if not isinstance(corpo_json, dict):
        corpo_json = {}

    try:
        validate_signature(
            x_signature=x_signature,
            x_request_id=x_request_id,
            data_id=data_id,
            secret=cfg.mp_webhook_secret,
        )
    except InvalidWebhookSignatureError as exc:
        # Log de aplicação apenas -- NUNCA um PaymentEvent, NUNCA consulta ao Mercado Pago,
        # NUNCA leitura/escrita de Payment/Entitlement (Etapa 10.3, correção 2).
        logger.warning("[WEBHOOK] assinatura recusada: %s", exc)
        return JSONResponse(
            status_code=401,
            content={"detail": "Assinatura inválida.", "code": "invalid_webhook_signature"},
            headers=NO_STORE,
        )

    # A partir daqui o webhook é "autenticado" -- PaymentEvent passa a existir.
    dados_corpo = corpo_json.get("data")
    data_id_corpo = dados_corpo.get("id") if isinstance(dados_corpo, dict) else None
    tipo = corpo_json.get("type")
    acao = corpo_json.get("action")
    payload_texto = corpo_bruto.decode("utf-8", errors="replace")

    event = _create_event(db, event_type=tipo, action=acao, resource_id=data_id, payload_raw=payload_texto)

    if data_id_corpo is not None and str(data_id_corpo) != data_id:
        _finish_event(db, event, result="conflict", error="data.id da query diverge do data.id do corpo.")
        return JSONResponse(status_code=200, content={"received": True}, headers=NO_STORE)

    try:
        resultado = reconcile_webhook_payment(db, mp_payment_id=data_id)
    except PaymentGatewayError as exc:
        logger.error("[WEBHOOK] falha ao consultar o Mercado Pago: %s", exc)
        _finish_event(db, event, result="error", error=str(exc)[:255])
        return JSONResponse(
            status_code=502,
            content={"detail": "Não foi possível confirmar o pagamento agora."},
            headers=NO_STORE,
        )

    _finish_event(db, event, result=resultado.outcome, error=resultado.detail)
    return JSONResponse(status_code=200, content={"received": True}, headers=NO_STORE)
