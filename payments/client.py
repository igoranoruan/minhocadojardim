"""Único arquivo do projeto que fala com o Mercado Pago de fato: conhece o SDK oficial
(`mercadopago`, PyPI), o Access Token, e a chamada HTTP em si. Nenhuma regra de negócio do nosso
sistema mora aqui -- não decide preço, não decide status local, não sabe o que é um Payment
interno. payments/gateway.py é quem chama este módulo, nunca o contrário.

Responsabilidade única: dado um payload já pronto (payments/gateway.py monta o payload) e uma
chave de idempotência, chamar POST /v1/payments do Mercado Pago e devolver a resposta crua (Etapa
10.2); ou, dado um mp_payment_id, chamar GET /v1/payments/{id} para a consulta autoritativa do
webhook (Etapa 10.3, get_payment). Só levanta PaymentGatewayError para uma falha de COMUNICAÇÃO
(exceção do SDK, erro de rede) -- uma recusa de pagamento (approved/pending/rejected/refunded/
charged_back/etc.) é uma resposta HTTP válida do Mercado Pago, nunca uma exceção; interpretar esse
valor é responsabilidade de payments/gateway.py, não deste módulo.
"""
import logging

import mercadopago

from config import get_settings
from payments.errors import PaymentGatewayError

logger = logging.getLogger("minhoca")


def create_payment(payment_data: dict, *, idempotency_key: str) -> dict:
    """Chama POST /v1/payments com o `payment_data` já pronto (payments/gateway.py decide o
    conteúdo) e `X-Idempotency-Key: idempotency_key` (payments/gateway.py sempre passa
    Payment.external_reference -- ver seu docstring para o porquê). Devolve o dicionário de
    resposta do SDK (`{"status": <http status>, "response": {...}}`), sem interpretar nada dele.

    Levanta PaymentGatewayError se a CHAMADA em si falhar (exceção do SDK/rede) -- nunca por uma
    recusa de pagamento em si, que chega aqui como uma resposta HTTP normal (201/200 com
    response["status"] == "rejected", por exemplo)."""
    sdk = mercadopago.SDK(get_settings().mp_access_token)
    request_options = mercadopago.config.RequestOptions()
    request_options.custom_headers = {"x-idempotency-key": idempotency_key}
    try:
        resultado = sdk.payment().create(payment_data, request_options)
    except Exception as exc:  # falha de comunicação com o Mercado Pago (rede, timeout, SDK) -- nunca uma recusa de pagamento
        logger.error("[PAYMENT] falha ao chamar o Mercado Pago: %s", exc.__class__.__name__, exc_info=True)
        raise PaymentGatewayError("Não foi possível se comunicar com o Mercado Pago.") from exc
    return resultado


def get_payment(mp_payment_id: str) -> dict:
    """Chama GET /v1/payments/{id} -- a consulta AUTORITATIVA usada pelo webhook (Etapa 10.3) para
    nunca decidir com base no status que a própria notificação alega. Mesmo formato de resposta de
    create_payment (`{"status": <http status>, "response": {...}}`), sem interpretar nada dela --
    quem traduz é payments/gateway.py.

    Levanta PaymentGatewayError se a CHAMADA em si falhar (rede/SDK) -- nunca por um pagamento em
    qualquer status (approved/pending/rejected/refunded/charged_back/etc. são respostas válidas,
    nunca erros)."""
    sdk = mercadopago.SDK(get_settings().mp_access_token)
    try:
        resultado = sdk.payment().get(mp_payment_id)
    except Exception as exc:  # falha de comunicação com o Mercado Pago (rede, timeout, SDK) -- nunca uma recusa de pagamento
        logger.error("[PAYMENT] falha ao consultar o Mercado Pago (GET): %s", exc.__class__.__name__, exc_info=True)
        raise PaymentGatewayError("Não foi possível consultar o Mercado Pago.") from exc
    return resultado
