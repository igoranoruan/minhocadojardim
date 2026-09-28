"""Arquitetura do checkout Mercado Pago (Etapa 10.2), mesmo espírito de
test_payments_architecture.py (10.1): tudo verificado ESTRUTURALMENTE (AST -- imports reais e
declarações de classe), nunca por substring de texto bruto (que pegaria falsos positivos em
docstrings/comentários que MENCIONAM esses nomes de propósito, para explicar o que NÃO fazer)."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLIENT_FILE = ROOT / "payments" / "client.py"
GATEWAY_FILE = ROOT / "payments" / "gateway.py"
SERVICE_FILE = ROOT / "payments" / "service.py"
REFERENCE_FILE = ROOT / "payments" / "reference.py"
ROUTES_FILE = ROOT / "routes" / "payments.py"

_PRECO_E_IDENTIDADE_PROIBIDOS = {
    "amount", "amount_cents", "price", "price_cents", "external_reference", "user_id",
}


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modulos: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modulos.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modulos.add(node.module)
    return modulos


def _class_field_names(path: Path, class_name: str) -> set[str]:
    """Nomes dos campos de uma classe Pydantic (BaseModel), pelas anotações de nível de módulo
    dentro da classe (`campo: tipo` / `campo: tipo = ...`) -- estrutural, não textual."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    classe = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == class_name)
    campos = set()
    for node in classe.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            campos.add(node.target.id)
    return campos


def test_client_e_o_unico_arquivo_que_importa_o_sdk_mercadopago():
    """Nenhum outro arquivo de payments/ ou routes/payments.py deve conhecer o SDK -- só
    payments/client.py (Etapa 10.2, item 5)."""
    assert "mercadopago" in _imported_modules(CLIENT_FILE)
    for arquivo in (GATEWAY_FILE, SERVICE_FILE, REFERENCE_FILE, ROUTES_FILE):
        modulos = _imported_modules(arquivo)
        assert "mercadopago" not in modulos, f"{arquivo} não deveria importar mercadopago"


def test_gateway_nao_importa_services_entitlements():
    """A 10.2 não concede/revoga entitlement -- isso é exclusivo da 10.3 (webhook)."""
    modulos = _imported_modules(GATEWAY_FILE)
    assert not any(m == "services.entitlements" or m.startswith("services.entitlements") for m in modulos), modulos


def test_routes_payments_nao_importa_services_entitlements():
    modulos = _imported_modules(ROUTES_FILE)
    assert not any(m == "services.entitlements" or m.startswith("services.entitlements") for m in modulos), modulos


def test_nenhum_import_de_grant_ou_revoke_entitlement_em_gateway_ou_rotas():
    """Checagem por IMPORT real (AST) do NOME importado, não por substring do texto (que pegaria
    os próprios comentários que dizem "NÃO chamar grant_entitlement", escritos de propósito)."""
    for arquivo in (GATEWAY_FILE, ROUTES_FILE):
        tree = ast.parse(arquivo.read_text(encoding="utf-8"))
        nomes_importados = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                nomes_importados.update(alias.name for alias in node.names)
        proibidos = {"grant_entitlement", "revoke_entitlement"} & nomes_importados
        assert not proibidos, f"{arquivo} importa {proibidos}"


def test_create_payment_route_body_nao_declara_campo_de_preco_ou_identidade():
    """POST /api/payments -- CreatePaymentBody só pode ter plan_code + method."""
    campos = _class_field_names(ROUTES_FILE, "CreatePaymentBody")
    proibidos = campos & _PRECO_E_IDENTIDADE_PROIBIDOS
    assert not proibidos, f"CreatePaymentBody aceita campo(s) proibido(s): {proibidos}"
    assert campos == {"plan_code", "method"}, campos


def test_submit_payment_route_body_nao_declara_campo_de_preco_ou_identidade():
    """POST /api/payments/{id}/submit -- SubmitPaymentBody nunca aceita plan_code/preço/
    external_reference/user_id (Etapa 10.2, item 3)."""
    campos = _class_field_names(ROUTES_FILE, "SubmitPaymentBody")
    proibidos = campos & (_PRECO_E_IDENTIDADE_PROIBIDOS | {"plan_code"})
    assert not proibidos, f"SubmitPaymentBody aceita campo(s) proibido(s): {proibidos}"


def test_payment_out_nao_expoe_external_reference():
    """A resposta da criação (Etapa 10.2, item 2) nunca inclui external_reference."""
    campos = _class_field_names(ROUTES_FILE, "PaymentOut")
    assert "external_reference" not in campos


def test_gateway_le_o_preco_do_payment_nunca_de_um_parametro_de_preco():
    """charge()/_build_payload() não têm (e não devem ganhar) nenhum parâmetro de preço --
    o valor cobrado só pode vir de payment.amount_cents, já resolvido pelo catálogo na criação
    (Etapa 10.1)."""
    tree = ast.parse(GATEWAY_FILE.read_text(encoding="utf-8"))
    for nome_funcao in ("charge", "_build_payload", "_transaction_amount"):
        funcao = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == nome_funcao)
        args = funcao.args
        nomes = {a.arg for a in [*args.posonlyargs, *args.args, *args.kwonlyargs]}
        proibidos = nomes & {"amount", "price", "amount_cents", "price_cents"}
        # _transaction_amount recebe amount_cents DE PROPÓSITO (é a função que faz a conversão) --
        # a trava real é que NENHUMA outra função aceite isso de fora.
        if nome_funcao != "_transaction_amount":
            assert not proibidos, f"{nome_funcao} aceita parâmetro de preço: {proibidos}"


def test_gateway_usa_o_external_reference_do_payment_como_idempotency_key():
    """Confirma POSITIVAMENTE (não só por ausência) que a chave de idempotência vem de
    payment.external_reference -- nunca gerada de novo, nunca vinda de fora."""
    tree = ast.parse(GATEWAY_FILE.read_text(encoding="utf-8"))
    funcao = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "charge")
    fonte = ast.unparse(funcao)
    assert "idempotency_key=payment.external_reference" in fonte.replace(" ", "")
