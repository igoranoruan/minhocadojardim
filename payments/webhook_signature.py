"""Camada isolada de validação da assinatura de webhook do Mercado Pago (Etapa 10.3).

Só faz UMA coisa: confirmar que uma notificação recebida em POST /api/webhooks/mercadopago foi
realmente assinada por quem tem o MP_WEBHOOK_SECRET. Não sabe o que é um Payment, não importa
SQLAlchemy nem nenhum model, não toca em banco -- é puramente criptográfico, chamado por
routes/webhooks.py ANTES de qualquer leitura ou escrita relacionada a Payment/Entitlement/
PaymentEvent (write amplification: uma tentativa de assinatura inválida nunca gera uma linha no
banco -- ver payments/errors.py::InvalidWebhookSignatureError).

Formato do header (documentação oficial do Mercado Pago):
    x-signature: ts=1704908010,v1=618c85345248dd820d5fd456117c2ab2ef8eda45a0282ff693eac24131a5e839

Manifest (Etapa 10.3, aprovado pelo CÉREBRO):
    id:{data.id};request-id:{x-request-id};ts:{ts};

HMAC-SHA256 do manifest com MP_WEBHOOK_SECRET, digest em hex, comparado ao `v1` recebido via
hmac.compare_digest (tempo constante -- nunca `==` de string, que vazaria timing).

`data.id` é normalizado para minúsculas antes de entrar no manifest -- irrelevante para
/v1/payments (IDs sempre numéricos), mas consistente com o comportamento documentado de outros
SDKs oficiais do Mercado Pago para outras APIs (ex.: Orders, que usa IDs alfanuméricos).
"""
import hashlib
import hmac

from payments.errors import InvalidWebhookSignatureError


def _parse_x_signature(x_signature: str) -> tuple[str, str]:
    """Extrai (ts, v1) de "ts=...,v1=...". A ORDEM dos pares não importa, mas os dois precisam
    estar presentes -- levanta InvalidWebhookSignatureError se um dos dois faltar."""
    partes: dict[str, str] = {}
    for pedaco in x_signature.split(","):
        if "=" not in pedaco:
            continue
        chave, _, valor = pedaco.partition("=")
        partes[chave.strip()] = valor.strip()

    ts = partes.get("ts")
    v1 = partes.get("v1")
    if not ts:
        raise InvalidWebhookSignatureError("x-signature sem 'ts'.")
    if not v1:
        raise InvalidWebhookSignatureError("x-signature sem 'v1'.")
    return ts, v1


def validate_signature(
    *,
    x_signature: str | None,
    x_request_id: str | None,
    data_id: str | None,
    secret: str,
) -> None:
    """Levanta InvalidWebhookSignatureError em QUALQUER situação que impeça confirmar a
    assinatura -- secret vazio, header ausente, campo ausente, ou assinatura que não bate. Nunca
    distingue o motivo para quem chama além do log: routes/webhooks.py sempre responde 401 com um
    detail genérico (o motivo exato nunca vai no corpo da resposta -- daria dica a quem está
    tentando adivinhar a assinatura)."""
    if not secret:
        raise InvalidWebhookSignatureError("MP_WEBHOOK_SECRET não configurado -- recusa por padrão.")
    if not x_signature:
        raise InvalidWebhookSignatureError("Header x-signature ausente.")
    if not x_request_id:
        raise InvalidWebhookSignatureError("Header x-request-id ausente.")
    if not data_id:
        raise InvalidWebhookSignatureError("data.id ausente (query string).")

    ts, v1_recebido = _parse_x_signature(x_signature)

    manifest = f"id:{data_id.lower()};request-id:{x_request_id};ts:{ts};"
    v1_calculado = hmac.new(secret.encode("utf-8"), manifest.encode("utf-8"), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(v1_calculado, v1_recebido):
        raise InvalidWebhookSignatureError("Assinatura não confere.")
