"""Arquitetura da Etapa 7: services/generation_flow.py não conhece HTTP; a rota fica fina."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVICE_FILE = ROOT / "services" / "generation_flow.py"
ROUTE_FILE = ROOT / "routes" / "generations.py"


def _root_modules(path: Path) -> set[str]:
    raizes: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            raizes.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            raizes.add(node.module.split(".")[0])
    return raizes


def test_generation_flow_nao_importa_routes_nem_fastapi():
    importados = _root_modules(SERVICE_FILE)
    assert not importados & {"routes", "fastapi", "starlette", "main"}, importados


def test_generation_flow_reaproveita_as_camadas_existentes_sem_duplicar():
    fonte = SERVICE_FILE.read_text(encoding="utf-8")
    assert "from services.usage import" in fonte
    assert "reserve_generation" in fonte and "complete_generation" in fonte and "fail_generation" in fonte
    assert "from download.service import download_video" in fonte
    assert "from processor.service import process_video" in fonte
    # nenhuma regra de cota nova: não cria nenhuma tabela/contador próprio
    for proibido in ("CREATE TABLE", "Column(", "mapped_column("):
        assert proibido not in fonte


def test_rota_e_fina_toda_regra_fica_no_servico():
    tree = ast.parse(ROUTE_FILE.read_text(encoding="utf-8"))
    funcao = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "create_generation")
    # a rota só deve chamar generate_from_url e montar a resposta -- poucas linhas de corpo
    assert len(funcao.body) <= 3, "routes/generations.py:create_generation deixou de ser fina"


def test_rota_nao_aceita_identidade_do_corpo():
    fonte = ROUTE_FILE.read_text(encoding="utf-8")
    tree = ast.parse(fonte)
    modelo = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "CreateGenerationBody")
    campos = [n.target.id for n in modelo.body if isinstance(n, ast.AnnAssign)]
    assert campos == ["url"]  # nada de user_id/email/account_id como campo do corpo
    assert "get_current_user" in fonte  # identidade vem exclusivamente da sessão


def test_generation_flow_nao_cria_nenhuma_migration_nova():
    versoes = sorted(p.name for p in (ROOT / "migrations" / "versions").glob("*.py"))
    assert versoes == ["0001_estrutura_inicial.py", "0002_autenticacao.py", "0003_resultado_geracao.py"]


def test_erros_respondem_no_formato_padrao_detail_code():
    fonte = ROUTE_FILE.read_text(encoding="utf-8")
    assert '"detail"' in fonte and '"code"' in fonte
    assert "Cache-Control" in fonte and "no-store" in fonte


def test_nenhuma_alteracao_de_regra_de_planos():
    """Etapa 7 não pode mexer no catálogo de planos nem nas regras de uso da Etapa 4.1."""
    for arquivo in ("services/plans.py", "services/usage.py"):
        importados = _root_modules(ROOT / arquivo)
        assert "generation_flow" not in importados  # ninguém "de baixo" conhece a Etapa 7
