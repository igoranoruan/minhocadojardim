import ast
from pathlib import Path

DATABASE_DIR = Path(__file__).resolve().parent.parent / "database"
FORBIDDEN_ROOTS = {"download", "processor"}


def _imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_database_nao_importa_download_nem_processor():
    arquivos = list(DATABASE_DIR.rglob("*.py"))
    assert len(arquivos) >= 8  # garante que o teste realmente varreu o pacote
    violacoes = {
        str(p.relative_to(DATABASE_DIR)): sorted(_imported_roots(p) & FORBIDDEN_ROOTS)
        for p in arquivos
        if _imported_roots(p) & FORBIDDEN_ROOTS
    }
    assert not violacoes, f"database importa módulos proibidos: {violacoes}"
