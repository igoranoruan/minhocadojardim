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


def test_generation_flow_usa_result_storage_so_para_save_e_delete():
    """Etapa 8B.2: o único uso de result_storage em generation_flow é save() (persistir o
    resultado) e delete() (limpeza do órfão se complete_generation falhar) -- nada de
    endpoint/streaming/entrega, que são etapas futuras (8B.3+)."""
    fonte = SERVICE_FILE.read_text(encoding="utf-8")
    assert "from services import result_storage" in fonte
    assert "result_storage.save(" in fonte
    assert "result_storage.delete(" in fonte
    for proibido in ("FileResponse", "StreamingResponse", "result_storage.load", "result_storage.open", "resolve_path"):
        assert proibido not in fonte


def test_rota_de_download_e_fina_toda_regra_fica_no_usage():
    tree = ast.parse(ROUTE_FILE.read_text(encoding="utf-8"))
    funcao = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "download_generation")
    assert len(funcao.body) <= 5, "routes/generations.py:download_generation deixou de ser fina"


def test_rota_de_download_nao_aceita_storage_key_da_url_ou_query():
    """storage_key só pode vir de dentro da Generation já ownership-checada -- nunca de um
    parâmetro da rota."""
    tree = ast.parse(ROUTE_FILE.read_text(encoding="utf-8"))
    funcao = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "download_generation")
    nomes_parametros = {a.arg for a in funcao.args.args}
    assert "storage_key" not in nomes_parametros
    assert nomes_parametros == {"generation_id", "user", "db"}


def test_busca_da_geracao_para_download_nao_e_duplicada_na_rota():
    """A lógica de ownership/status/expiração mora só em services.usage.get_downloadable_generation
    -- a rota não pode reimplementar nenhuma dessas checagens por conta própria."""
    fonte_rota = ROUTE_FILE.read_text(encoding="utf-8")
    assert "get_downloadable_generation(" in fonte_rota
    assert "output_expires_at" not in fonte_rota  # a rota nunca compara isso sozinha
    assert ".status ==" not in fonte_rota and ".status !=" not in fonte_rota


def test_nenhuma_alteracao_de_regra_de_planos():
    """Etapa 7 não pode mexer no catálogo de planos nem nas regras de uso da Etapa 4.1."""
    for arquivo in ("services/plans.py", "services/usage.py"):
        importados = _root_modules(ROOT / arquivo)
        assert "generation_flow" not in importados  # ninguém "de baixo" conhece a Etapa 7
