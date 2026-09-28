"""payments/webhook_signature.py (Etapa 10.3): validação pura da assinatura x-signature -- sem
banco, sem rede, sem sessão nenhuma. hmac.compare_digest garante comparação em tempo constante;
aqui só testamos o RESULTADO (aceita/recusa), nunca o tempo de execução.
"""
import hashlib
import hmac

import pytest

from payments.errors import InvalidWebhookSignatureError
from payments.webhook_signature import validate_signature

SECRET = "segredo-de-teste-nao-usar-em-producao"


def _assinar(*, data_id: str, x_request_id: str, ts: str, secret: str = SECRET) -> str:
    manifest = f"id:{data_id.lower()};request-id:{x_request_id};ts:{ts};"
    v1 = hmac.new(secret.encode(), manifest.encode(), hashlib.sha256).hexdigest()
    return f"ts={ts},v1={v1}"


def test_assinatura_valida_nao_levanta():
    x_sig = _assinar(data_id="123456789", x_request_id="req-abc", ts="1700000000")
    validate_signature(x_signature=x_sig, x_request_id="req-abc", data_id="123456789", secret=SECRET)


def test_manifest_e_exatamente_id_request_id_ts_com_ponto_e_virgula_final():
    """Se a implementação usasse outro formato de manifest, uma assinatura calculada aqui (fora de
    webhook_signature.py) com o formato OFICIAL deixaria de bater."""
    manifest_oficial = "id:987654321;request-id:req-xyz;ts:1700000001;"
    v1 = hmac.new(SECRET.encode(), manifest_oficial.encode(), hashlib.sha256).hexdigest()
    x_sig = f"ts=1700000001,v1={v1}"
    validate_signature(x_signature=x_sig, x_request_id="req-xyz", data_id="987654321", secret=SECRET)


def test_ordem_dos_pares_no_header_nao_importa():
    manifest = "id:111;request-id:req-ordem;ts:1700000005;"
    v1 = hmac.new(SECRET.encode(), manifest.encode(), hashlib.sha256).hexdigest()
    x_sig_invertido = f"v1={v1},ts=1700000005"
    validate_signature(x_signature=x_sig_invertido, x_request_id="req-ordem", data_id="111", secret=SECRET)


def test_data_id_e_normalizado_para_minusculas_no_manifest():
    """/v1/payments usa IDs sempre numéricos (minúsculas é irrelevante na prática), mas a
    normalização é aplicada de qualquer forma -- consistência defensiva com outros SDKs do
    Mercado Pago (Orders API usa IDs alfanuméricos)."""
    manifest_minusculo = "id:abc123;request-id:req-case;ts:1700000002;"
    v1 = hmac.new(SECRET.encode(), manifest_minusculo.encode(), hashlib.sha256).hexdigest()
    x_sig = f"ts=1700000002,v1={v1}"
    validate_signature(x_signature=x_sig, x_request_id="req-case", data_id="ABC123", secret=SECRET)


def test_assinatura_errada_e_recusada():
    x_sig = _assinar(data_id="123456789", x_request_id="req-abc", ts="1700000000")
    ts_txt, _, _ = x_sig.partition(",")
    x_sig_adulterado = f"{ts_txt},v1=" + "0" * 64
    with pytest.raises(InvalidWebhookSignatureError):
        validate_signature(x_signature=x_sig_adulterado, x_request_id="req-abc", data_id="123456789", secret=SECRET)


def test_data_id_diferente_do_assinado_e_recusado():
    """Mesma assinatura, mas para um data.id diferente -- simula um atacante reaproveitando uma
    assinatura válida de outra notificação."""
    x_sig = _assinar(data_id="111", x_request_id="req-abc", ts="1700000000")
    with pytest.raises(InvalidWebhookSignatureError):
        validate_signature(x_signature=x_sig, x_request_id="req-abc", data_id="222", secret=SECRET)


def test_secret_vazio_recusa_mesmo_com_assinatura_bem_formada():
    x_sig = _assinar(data_id="123456789", x_request_id="req-abc", ts="1700000000")
    with pytest.raises(InvalidWebhookSignatureError):
        validate_signature(x_signature=x_sig, x_request_id="req-abc", data_id="123456789", secret="")


def test_x_signature_ausente():
    with pytest.raises(InvalidWebhookSignatureError):
        validate_signature(x_signature=None, x_request_id="req-abc", data_id="123456789", secret=SECRET)


def test_ts_ausente_no_header():
    with pytest.raises(InvalidWebhookSignatureError):
        validate_signature(x_signature="v1=" + "a" * 64, x_request_id="req-abc", data_id="123456789", secret=SECRET)


def test_v1_ausente_no_header():
    with pytest.raises(InvalidWebhookSignatureError):
        validate_signature(x_signature="ts=1700000000", x_request_id="req-abc", data_id="123456789", secret=SECRET)


def test_data_id_ausente():
    x_sig = _assinar(data_id="123456789", x_request_id="req-abc", ts="1700000000")
    with pytest.raises(InvalidWebhookSignatureError):
        validate_signature(x_signature=x_sig, x_request_id="req-abc", data_id=None, secret=SECRET)


def test_x_request_id_ausente():
    x_sig = _assinar(data_id="123456789", x_request_id="req-abc", ts="1700000000")
    with pytest.raises(InvalidWebhookSignatureError):
        validate_signature(x_signature=x_sig, x_request_id=None, data_id="123456789", secret=SECRET)
