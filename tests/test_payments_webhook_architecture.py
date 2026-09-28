"""Arquitetura do webhook Mercado Pago (Etapa 10.3), mesmo espírito estrutural (AST) de
test_payments_architecture.py (10.1) e test_payments_gateway_architecture.py (10.2)."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEBHOOK_SIGNATURE_FILE = ROOT / "payments" / "webhook_signature.py"
WEBHOOKS_ROUTE_FILE = ROOT / "routes" / "webhooks.py"
GATEWAY_FILE = ROOT / "payments" / "gateway.py"
SERVICE_FILE = ROOT / "payments" / "service.py"


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modulos: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modulos.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modulos.add(node.module)
    return modulos


def _imported_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    nomes: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            nomes.update(alias.name for alias in node.names)
    return nomes


def test_webhook_signature_e_uma_camada_pura_sem_banco():
    """payments/webhook_signature.py não sabe o que é um Payment nem fala com o banco -- só
    hashlib/hmac e a própria exceção que ele levanta."""
    modulos = _imported_modules(WEBHOOK_SIGNATURE_FILE)
    proibidos = {m for m in modulos if m.startswith("sqlalchemy") or m.startswith("database")}
    assert not proibidos, f"webhook_signature.py importa camada de banco: {proibidos}"


def test_rota_webhook_nao_usa_get_current_user():
    """POST /api/webhooks/mercadopago é servidor-a-servidor -- nunca autenticação de usuário."""
    nomes = _imported_names(WEBHOOKS_ROUTE_FILE)
    assert "get_current_user" not in nomes


def test_rota_webhook_nao_importa_services_entitlements_diretamente():
    """Rota fina: quem concede/revoga é sempre payments.gateway.reconcile_webhook_payment."""
    modulos = _imported_modules(WEBHOOKS_ROUTE_FILE)
    assert not any(m == "services.entitlements" or m.startswith("services.entitlements") for m in modulos)


def test_find_payment_for_webhook_nunca_filtra_por_user_id_ou_status():
    """Diferente de get_owned_pending_payment (10.2): o webhook não tem usuário autenticado, e o
    Payment já pode não estar mais "pending" -- checagem estrutural dos PARÂMETROS da função."""
    tree = ast.parse(SERVICE_FILE.read_text(encoding="utf-8"))
    funcao = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "find_payment_for_webhook")
    args = funcao.args
    nomes = {a.arg for a in [*args.posonlyargs, *args.args, *args.kwonlyargs]}
    assert "user_id" not in nomes
    assert "status" not in nomes
    assert {"mp_payment_id", "external_reference"} <= nomes


def test_reconcile_usa_lock_user_row_depois_de_localizar_o_payment():
    """Confirma POSITIVAMENTE (Etapa 10.3, correção 3) que lock_user_row aparece DEPOIS da
    chamada a find_payment_for_webhook no código-fonte da função -- nunca antes, porque o
    user_id só é conhecido depois de localizar o Payment."""
    tree = ast.parse(GATEWAY_FILE.read_text(encoding="utf-8"))
    funcao = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "reconcile_webhook_payment")
    fonte = ast.unparse(funcao)
    pos_find = fonte.index("find_payment_for_webhook(")
    pos_lock = fonte.index("lock_user_row(")
    assert pos_lock > pos_find, "lock_user_row aparece antes de find_payment_for_webhook"
