"""Arquitetura da Etapa 4: camadas, sem dependência circular e sem regra comercial fora dos serviços."""
import ast
import re
from pathlib import Path

from database.base import Base

ROOT = Path(__file__).resolve().parent.parent
BUSINESS = ("plans", "entitlement_chain", "entitlements", "locks", "usage")


def _project_files():
    skip = {"tests", "migrations", ".venv", "venv", "__pycache__", "data", "static"}
    return [p for p in ROOT.rglob("*.py") if not skip & set(p.relative_to(ROOT).parts)]


def _module_name(path: Path) -> str:
    parts = list(path.relative_to(ROOT).with_suffix("").parts)
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def _imports(path: Path, known: set[str]) -> set[str]:
    """Módulos DO PROJETO importados pelo arquivo."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module] + [f"{node.module}.{alias.name}" for alias in node.names]
        else:
            continue
        found.update(n for n in names if n in known)
    return found


def _graph():
    files = _project_files()
    known = {_module_name(p) for p in files}
    return {_module_name(p): _imports(p, known) - {_module_name(p)} for p in files}


def _source(name: str) -> str:
    return (ROOT / "services" / f"{name}.py").read_text(encoding="utf-8")


def _root_modules(path: Path) -> set[str]:
    """Primeiro segmento de tudo o que o arquivo importa (ex.: 'fastapi', 'sqlalchemy', 'services')."""
    raizes: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            raizes.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            raizes.add(node.module.split(".")[0])
    return raizes


def test_nao_ha_dependencia_circular_entre_os_modulos_do_projeto():
    graph = _graph()
    visiting, done = [], set()

    def visit(module):
        if module in done:
            return
        assert module not in visiting, f"dependência circular: {' -> '.join(visiting[visiting.index(module):] + [module])}"
        visiting.append(module)
        for dep in graph.get(module, ()):
            visit(dep)
        visiting.pop()
        done.add(module)

    for module in graph:
        visit(module)
    assert "services.usage" in graph  # garante que o teste enxergou os serviços novos


def test_servicos_de_negocio_nao_dependem_de_rotas_nem_de_http():
    for nome in BUSINESS:
        importados = _root_modules(ROOT / "services" / f"{nome}.py")
        assert not importados & {"routes", "main", "fastapi", "starlette"}, (nome, importados)


def test_database_nao_importa_services():
    graph = _graph()
    for modulo, deps in graph.items():
        if modulo.startswith("database"):
            assert not any(d.startswith("services") for d in deps), modulo



# Consumidores legítimos de services.usage/services.entitlements fora de services/ (Etapa 7 —
# orquestração da geração): a rota que expõe a geração individual, e o composition root
# (main.py), que precisa das CLASSES de erro (QuotaExceededError, EntitlementInconsistencyError)
# para registrar app.add_exception_handler — mesmo padrão já usado para AuthError desde a Etapa 3.
# Nenhum dos dois CHAMA reserve_generation/complete_generation/fail_generation diretamente; quem
# faz isso é services/generation_flow.py (ver tests/test_generation_flow_architecture.py). Toda
# outra rota (routes.auth, routes.health, routes.deps) continua proibida.
USAGE_ALLOWED_CONSUMERS = {"main", "routes.generations"}


def test_nenhuma_regra_de_cota_em_rotas_main_ou_frontend():
    graph = _graph()
    for modulo in [m for m in graph if m.startswith("routes") or m == "main"]:
        if modulo in USAGE_ALLOWED_CONSUMERS:
            continue
        proibidos = {d for d in graph[modulo] if d in {f"services.{n}" for n in BUSINESS}}
        assert not proibidos, f"{modulo} não pode importar regra de cota: {proibidos}"
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    assert "/api/usage" not in html and "reserve_generation" not in html  # nada de cota no frontend novo


def test_regras_comerciais_nao_usam_datetime_now_diretamente():
    for nome in BUSINESS:
        fonte = _source(nome)
        for chamada in ("datetime.now(", "datetime.utcnow(", "utcnow(", "time.time("):
            assert chamada not in fonte, f"services/{nome}.py usa {chamada} (use utils.time_sp)"


def test_o_tempo_das_regras_vem_de_utils_time_sp():
    for nome in ("entitlements", "usage"):
        assert "from utils.time_sp import" in _source(nome)


def test_sem_redis_celery_fila_ou_servico_externo():
    proibidos = {"redis", "celery", "rq", "kombu", "dramatiq", "boto3", "requests", "httpx"}
    pacotes = {
        re.split(r"[<>=\[!~; ]", linha.strip().lower(), maxsplit=1)[0]
        for linha in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if linha.strip() and not linha.strip().startswith("#")
    }
    assert not pacotes & proibidos, pacotes & proibidos
    for arquivo in _project_files():
        assert not _root_modules(arquivo) & proibidos, arquivo


def test_a_etapa_4_nao_criou_tabelas_nem_migration():
    versoes = sorted(p.name for p in (ROOT / "migrations" / "versions").glob("*.py"))
    assert versoes == ["0001_estrutura_inicial.py", "0002_autenticacao.py", "0003_resultado_geracao.py"]
    assert set(Base.metadata.tables) == {
        "users", "payments", "entitlements", "generations", "batches", "payment_events", "login_codes", "auth_sessions",
    }


def test_time_sp_preserva_a_api_anterior():
    from utils import time_sp

    assert callable(time_sp.now_sp) and time_sp.SP is not None
    assert {"to_sp", "resolve_now", "sp_day", "sp_week_start"} <= set(dir(time_sp))
