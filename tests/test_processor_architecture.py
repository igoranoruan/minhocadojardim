"""Arquitetura da camada de processamento: isolamento, sem shell, sem cleaned_hash, sem migration."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROCESSOR_DIR = ROOT / "processor"
FORBIDDEN_FOR_PROCESSOR = {"routes", "services", "database", "main"}


def _processor_files():
    return sorted(PROCESSOR_DIR.glob("*.py"))


def _root_modules(path: Path) -> set[str]:
    raizes: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            raizes.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            raizes.add(node.module.split(".")[0])
    return raizes


def test_processor_existe_com_os_modulos_esperados():
    nomes = {p.name for p in _processor_files()}
    esperados = {
        "__init__.py", "errors.py", "result.py", "probe.py", "ffmpeg.py",
        "validation.py", "tempfiles.py", "service.py",
    }
    assert esperados <= nomes


def test_processor_nao_importa_routes_services_database_ou_main():
    for arquivo in _processor_files():
        importados = _root_modules(arquivo)
        assert not importados & FORBIDDEN_FOR_PROCESSOR, (arquivo.name, importados)


def test_processor_nao_importa_download():
    """processor/ transforma o arquivo que a Etapa 5 entrega, mas não precisa saber de onde ele
    veio — recebe só um Path. Quem liga as duas camadas é uma etapa futura."""
    for arquivo in _processor_files():
        assert "download" not in _root_modules(arquivo), arquivo.name



# Consumidores legítimos de processor/ fora da própria pasta (Etapa 7 — orquestração da geração):
# o orquestrador em si, a rota que o expõe, e o composition root (main.py), que precisa da CLASSE
# de erro para registrar app.add_exception_handler (mesmo padrão já usado para AuthError desde a
# Etapa 3). Qualquer outro arquivo do projeto continua proibido de importar processor/.
PROCESSOR_ALLOWED_CONSUMERS = {"main.py", "routes/generations.py", "services/generation_flow.py"}


def test_nada_no_projeto_ainda_importa_processor():
    """Só os consumidores legítimos (PROCESSOR_ALLOWED_CONSUMERS) podem importar processor/;
    todo o resto do projeto continua proibido — isolamento de camada, não uma lista permissiva."""
    skip_dirs = {"tests", "migrations", "processor", "download", ".venv", "venv", "__pycache__", "data", "static"}
    for arquivo in ROOT.rglob("*.py"):
        if set(arquivo.relative_to(ROOT).parts) & skip_dirs:
            continue
        caminho_relativo = arquivo.relative_to(ROOT).as_posix()
        if caminho_relativo in PROCESSOR_ALLOWED_CONSUMERS:
            continue
        assert "processor" not in _root_modules(arquivo), arquivo


def _production_files():
    """Código de PRODUTO: exclui tests/ (que precisa poder mencionar "shell=True" ou
    "cleaned_hash" nas suas próprias asserções, sem que isso acuse a si mesmo)."""
    skip_dirs = {"tests", "migrations", ".venv", "venv", "__pycache__", "data"}
    return [p for p in ROOT.rglob("*.py") if not set(p.relative_to(ROOT).parts) & skip_dirs]


def test_producao_tem_pelo_menos_os_arquivos_esperados():
    nomes = {p.name for p in _production_files()}
    assert {"main.py", "config.py"} <= nomes
    assert {p.name for p in PROCESSOR_DIR.glob("*.py")} <= nomes


def test_nenhum_shell_true_ou_os_system_no_processor():
    """Os docstrings dos módulos explicam a regra sem usar a substring literal "shell=True"
    (viraria um falso positivo desta própria checagem) — por isso a busca direta já é válida."""
    for arquivo in _processor_files():
        fonte = arquivo.read_text(encoding="utf-8")
        assert "shell=True" not in fonte, arquivo.name
        assert "os.system(" not in fonte, arquivo.name


def test_subprocess_sempre_recebe_lista_nunca_string_montada():
    """Checagem estática: toda chamada a subprocess.run/Popen no processor passa uma lista/nome
    como primeiro argumento posicional, nunca uma f-string/concatenação."""
    for arquivo in _processor_files():
        tree = ast.parse(arquivo.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("run", "Popen")
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "subprocess"
            ):
                assert node.args, (arquivo.name, "chamada sem argumento posicional")
                primeiro = node.args[0]
                assert isinstance(primeiro, (ast.List, ast.Name)), (
                    arquivo.name, "primeiro argumento de subprocess não é uma lista/variável", ast.dump(primeiro)
                )


def test_nenhum_conceito_cleaned_hash_no_projeto():
    for arquivo in _production_files():
        assert "cleaned_hash" not in arquivo.read_text(encoding="utf-8").lower(), arquivo


def test_output_sha256_e_o_unico_hash_do_resultado():
    fonte = (PROCESSOR_DIR / "result.py").read_text(encoding="utf-8")
    tree = ast.parse(fonte)
    classe = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ProcessingResult")
    campos = [n.target.id for n in classe.body if isinstance(n, ast.AnnAssign)]
    hashes = [c for c in campos if "hash" in c.lower() or "sha" in c.lower()]
    assert hashes == ["output_sha256"]


def test_nenhuma_migration_nova_foi_criada():
    versoes = sorted(p.name for p in (ROOT / "migrations" / "versions").glob("*.py"))
    assert versoes == ["0001_estrutura_inicial.py", "0002_autenticacao.py"]


def test_processor_nao_usa_datetime_now_diretamente():
    for arquivo in _processor_files():
        fonte = arquivo.read_text(encoding="utf-8")
        for proibido in ("datetime.now(", "datetime.utcnow(", "time.time("):
            assert proibido not in fonte, (arquivo.name, proibido)


def test_processo_video_devolve_processingresult():
    fonte = (PROCESSOR_DIR / "service.py").read_text(encoding="utf-8")
    tree = ast.parse(fonte)
    funcao = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "process_video")
    assert ast.unparse(funcao.returns) == "ProcessingResult"


def test_config_reaproveita_os_limites_ja_existentes_da_etapa_5():
    """MAX_VIDEO_SIZE_BYTES e MAX_VIDEO_DURATION_SECONDS não podem existir em duplicidade."""
    fonte_config = (ROOT / "config.py").read_text(encoding="utf-8")
    assert fonte_config.count("MAX_VIDEO_SIZE_BYTES =") == 1
    assert fonte_config.count("MAX_VIDEO_DURATION_SECONDS =") == 1
    fonte_validation = (PROCESSOR_DIR / "validation.py").read_text(encoding="utf-8")
    assert "from config import" in fonte_validation and "MAX_VIDEO_SIZE_BYTES" in fonte_validation
