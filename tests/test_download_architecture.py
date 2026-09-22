"""Arquitetura da camada de download: isolamento do resto do sistema, yt-dlp só onde deve estar,
sem cookies pessoais, sem dependências novas além de yt-dlp, sem migration.
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOWNLOAD_DIR = ROOT / "download"
FORBIDDEN_FOR_DOWNLOAD = {"services", "database", "routes", "main"}
FILES_ALLOWED_TO_IMPORT_YTDLP = {"ytdlp_downloader.py"}


def _download_files():
    return sorted(DOWNLOAD_DIR.glob("*.py"))


def _root_modules(path: Path) -> set[str]:
    raizes: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            raizes.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            raizes.add(node.module.split(".")[0])
    return raizes


def test_download_existe_com_os_modulos_esperados():
    nomes = {p.name for p in _download_files()}
    esperados = {
        "__init__.py", "errors.py", "result.py", "platform.py", "url_safety.py",
        "tempfiles.py", "file_validation.py", "base.py", "ytdlp_downloader.py", "service.py",
    }
    assert esperados <= nomes


def test_download_nao_importa_services_database_routes_ou_main():
    for arquivo in _download_files():
        importados = _root_modules(arquivo)
        assert not importados & FORBIDDEN_FOR_DOWNLOAD, (arquivo.name, importados)


def test_resto_do_sistema_nao_importa_download():
    """Etapa 6 é quem vai importar download/; nada existente hoje deve importar."""
    skip_dirs = {"tests", "migrations", "download", ".venv", "venv", "__pycache__", "data", "static"}
    for arquivo in ROOT.rglob("*.py"):
        if set(arquivo.relative_to(ROOT).parts) & skip_dirs:
            continue
        assert "download" not in _root_modules(arquivo), arquivo


def test_so_ytdlp_downloader_importa_yt_dlp():
    for arquivo in _download_files():
        importa = "yt_dlp" in _root_modules(arquivo)
        deveria = arquivo.name in FILES_ALLOWED_TO_IMPORT_YTDLP
        assert importa == deveria, (arquivo.name, "importa yt_dlp" if importa else "não importa yt_dlp")


def test_service_nao_importa_yt_dlp_diretamente():
    """download/service.py conhece só a interface (PlatformDownloader) e o registro concreto,
    nunca o pacote yt_dlp em si — o contrato público não vaza tipos do yt-dlp."""
    assert "yt_dlp" not in _root_modules(DOWNLOAD_DIR / "service.py")


def test_download_result_e_o_unico_tipo_publico_devolvido_pelo_service():
    fonte = (DOWNLOAD_DIR / "service.py").read_text(encoding="utf-8")
    tree = ast.parse(fonte)
    funcao = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "download_video")
    anotacao = ast.unparse(funcao.returns)
    assert anotacao == "DownloadResult"


def _production_files():
    """Código de PRODUTO (nunca testado por si mesmo): exclui tests/, migrations/, __pycache__
    e ambientes virtuais. `tests/` fica de fora de propósito — este próprio arquivo de teste
    precisa poder mencionar a string "cookies.txt" para verificar sua ausência em outro lugar,
    o que faria a varredura acusar a si mesma se `tests/` entrasse na busca."""
    skip_dirs = {"tests", "migrations", ".venv", "venv", "__pycache__", "data"}
    return [
        p for p in ROOT.rglob("*.py")
        if not set(p.relative_to(ROOT).parts) & skip_dirs
    ]


def test_producao_tem_pelo_menos_os_arquivos_esperados():
    """Evita que a exclusão de diretórios acima esvazie a varredura por engano."""
    nomes = {p.name for p in _production_files()}
    assert {"main.py", "config.py"} <= nomes
    assert {p.name for p in DOWNLOAD_DIR.glob("*.py")} <= nomes


def test_nenhum_codigo_de_producao_referencia_cookies_txt_como_padrao():
    """Nenhum caminho de código do produto deve usar um arquivo de cookies pessoais como
    mecanismo padrão de autenticação (nem no download, nem em nenhuma outra etapa)."""
    for arquivo in _production_files():
        assert "cookies.txt" not in arquivo.read_text(encoding="utf-8"), arquivo


def test_allowed_extractors_nunca_referencia_o_extractor_generico_no_codigo_fonte():
    fonte = (DOWNLOAD_DIR / "service.py").read_text(encoding="utf-8")
    assert '"generic"' not in fonte and "'generic'" not in fonte


def test_requirements_ganhou_so_yt_dlp():
    linhas = [
        linha.strip() for linha in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if linha.strip() and not linha.strip().startswith("#")
    ]
    pacotes = {linha.split(">=")[0].split("==")[0].split("[")[0].lower() for linha in linhas}
    assert "yt-dlp" in pacotes
    assert not pacotes & {"requests", "httpx"}


def test_nenhuma_migration_nova_foi_criada():
    versoes = sorted(p.name for p in (ROOT / "migrations" / "versions").glob("*.py"))
    assert versoes == ["0001_estrutura_inicial.py", "0002_autenticacao.py"]


def test_download_nao_usa_datetime_now_diretamente():
    for arquivo in _download_files():
        fonte = arquivo.read_text(encoding="utf-8")
        for proibido in ("datetime.now(", "datetime.utcnow(", "time.time("):
            assert proibido not in fonte, (arquivo.name, proibido)
