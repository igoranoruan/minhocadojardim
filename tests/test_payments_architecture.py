"""Arquitetura da fundação de pagamentos (Etapa 10.1), no mesmo espírito de
test_batch_download_architecture.py / test_usage_architecture.py: verifica ESTRUTURALMENTE (via
AST/leitura de texto, nunca "rodando" o código) as garantias que a especificação exigiu:

- create_payment nunca aceita preço como argumento (trava contra cliente enviando amount
  arbitrário -- ver payments/service.py).
- payments/service.py não importa nada de services.entitlements nem do Mercado Pago -- a
  fundação desta etapa não concede entitlement nem fala com o gateway; isso é escopo de etapas
  futuras (10.2+).
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVICE_FILE = ROOT / "payments" / "service.py"

_PRECO_PROIBIDO = {"amount_cents", "price_cents", "amount", "price"}


def _function_node(path: Path, name: str) -> ast.FunctionDef:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)


def _param_names(funcao: ast.FunctionDef) -> set[str]:
    args = funcao.args
    todos = [*args.posonlyargs, *args.args, *args.kwonlyargs]
    nomes = {a.arg for a in todos}
    if args.vararg:
        nomes.add(args.vararg.arg)
    if args.kwarg:
        nomes.add(args.kwarg.arg)
    return nomes


def test_create_payment_nao_aceita_nenhum_parametro_de_preco():
    """Nem amount_cents, nem price_cents, nem amount, nem price -- o preço só pode vir de
    services.plans.get_plan(plan_code) DENTRO da função, nunca de fora dela."""
    funcao = _function_node(SERVICE_FILE, "create_payment")
    nomes = _param_names(funcao)
    proibidos_presentes = nomes & _PRECO_PROIBIDO
    assert not proibidos_presentes, (
        f"create_payment aceita parâmetro(s) de preço, o que permitiria um cliente influenciar "
        f"o valor cobrado: {proibidos_presentes}"
    )


def test_create_payment_le_o_preco_do_catalogo():
    """Confirma (positivamente, não só por ausência) que o preço realmente vem de
    plan.price_cents, lido de dentro da função -- não um valor fixo nem outra origem."""
    fonte = ast.unparse(_function_node(SERVICE_FILE, "create_payment"))
    assert "plan.price_cents" in fonte
    assert "get_plan(" in fonte


def _imported_modules(path: Path) -> set[str]:
    """Nomes de módulo de todo `import x` / `from x import ...` do arquivo -- estrutural (AST),
    nunca por substring do texto bruto (que pegaria falsos positivos em docstrings/comentários,
    ex.: este próprio arquivo de teste MENCIONA "grant_entitlement" em prosa, o que um grep
    textual confundiria com um import real)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modulos: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modulos.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modulos.add(node.module)
    return modulos


def test_payments_service_nao_importa_entitlements():
    """A fundação (10.1) não concede entitlement -- isso é decisão explícita de uma etapa
    futura (10.2+), nunca implícita aqui. Checagem por IMPORT real (AST), não por texto: o
    docstring de payments/service.py cita "services.entitlements.grant_entitlement" em prosa,
    de propósito, para explicar o que NÃO fazer -- isso nunca deveria contar como violação."""
    modulos = _imported_modules(SERVICE_FILE)
    assert not any(m.startswith("services.entitlements") or m == "services.entitlements" for m in modulos), modulos


def test_payments_service_nao_importa_mercado_pago():
    """A fundação (10.1) não fala com nenhum gateway -- nenhum SDK, nenhum módulo de rede.
    Checagem por IMPORT real (AST): nenhum módulo importado por payments/service.py deve ser um
    SDK de pagamento/rede conhecido."""
    modulos = _imported_modules(SERVICE_FILE)
    proibidos = {"mercadopago", "requests", "httpx", "payments.gateway"}
    encontrados = modulos & proibidos
    assert not encontrados, encontrados


def test_create_payment_usa_lock_user_row():
    """Toda escrita concorrente de Payment precisa estar serializada por usuário -- mesmo padrão
    já usado por reserve_batch/grant_entitlement."""
    fonte = ast.unparse(_function_node(SERVICE_FILE, "create_payment"))
    assert "lock_user_row(" in fonte


def test_create_payment_usa_payment_methods_existente_em_vez_de_duplicar_a_lista():
    """Nunca reimplementa a lista de métodos válidos -- reaproveita
    database.models.payment.PAYMENT_METHODS, a MESMA que já valida o banco (CheckConstraint).
    Os literais "pix"/"credit_card" nunca aparecem soltos no código (só viriam de uma lista
    duplicada) -- a única forma de conhecer um método válido é através de PAYMENT_METHODS."""
    fonte = SERVICE_FILE.read_text(encoding="utf-8")
    assert "from database.models.payment import PAYMENT_METHODS" in fonte
    assert '"pix"' not in fonte and "'pix'" not in fonte
    assert '"credit_card"' not in fonte and "'credit_card'" not in fonte
