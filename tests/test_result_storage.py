"""services/result_storage.py: save/exists/delete (Etapa 8B.2). Puro filesystem, sem banco."""
import ast
from pathlib import Path

import pytest

from services import result_storage

ROOT = Path(__file__).resolve().parent.parent
RESULT_STORAGE_FILE = ROOT / "services" / "result_storage.py"


@pytest.fixture(autouse=True)
def diretorio_de_storage(tmp_path, monkeypatch):
    monkeypatch.setattr("services.result_storage.RESULT_STORAGE_DIR", str(tmp_path / "results"))


def _arquivo(tmp_path, nome="origem.mp4", conteudo=b"conteudo de video de teste"):
    caminho = tmp_path / nome
    caminho.write_bytes(conteudo)
    return caminho


# ============================================================================ save()
def test_save_move_o_arquivo_nao_copia(tmp_path):
    origem = _arquivo(tmp_path)
    chave = result_storage.save(origem, size_bytes=len(b"conteudo de video de teste"))
    assert not origem.exists()  # não sobrou cópia no caminho de origem
    assert result_storage.exists(chave)


def test_save_preserva_o_conteudo_exato(tmp_path):
    conteudo = b"bytes exatos do mp4 processado"
    origem = _arquivo(tmp_path, conteudo=conteudo)
    chave = result_storage.save(origem, size_bytes=len(conteudo))
    caminho_salvo = Path(result_storage.RESULT_STORAGE_DIR) / f"{chave}.mp4"
    assert caminho_salvo.read_bytes() == conteudo


def test_save_gera_uma_chave_nova_a_cada_chamada(tmp_path):
    chaves = set()
    for i in range(20):
        origem = _arquivo(tmp_path, nome=f"v{i}.mp4")
        chaves.add(result_storage.save(origem, size_bytes=1))
    assert len(chaves) == 20


def test_nome_fisico_nunca_deriva_do_nome_de_entrada(tmp_path):
    origem = _arquivo(tmp_path, nome="nome-sensivel-do-usuario.mp4")
    chave = result_storage.save(origem, size_bytes=1)
    assert "nome-sensivel-do-usuario" not in chave
    caminho_salvo = Path(result_storage.RESULT_STORAGE_DIR) / f"{chave}.mp4"
    assert "nome-sensivel-do-usuario" not in caminho_salvo.name


def test_chave_e_um_uuid4_hex_de_32_caracteres(tmp_path):
    origem = _arquivo(tmp_path)
    chave = result_storage.save(origem, size_bytes=1)
    assert len(chave) == 32
    assert all(c in "0123456789abcdef" for c in chave)


def test_arquivo_salvo_tem_extensao_mp4_sempre(tmp_path):
    origem = _arquivo(tmp_path, nome="qualquer-nome-sem-extensao-relevante")
    chave = result_storage.save(origem, size_bytes=1)
    assert (Path(result_storage.RESULT_STORAGE_DIR) / f"{chave}.mp4").exists()


def test_diretorio_de_storage_e_criado_automaticamente(tmp_path):
    destino = tmp_path / "ainda-nao-existe" / "results"
    import services.result_storage as rs
    rs.RESULT_STORAGE_DIR = str(destino)
    try:
        origem = _arquivo(tmp_path)
        rs.save(origem, size_bytes=1)
        assert destino.is_dir()
    finally:
        rs.RESULT_STORAGE_DIR = str(tmp_path / "results")


# ============================================================================ resolve_path() (Etapa 8B.3)
def test_resolve_path_devolve_o_caminho_certo_com_o_conteudo_certo(tmp_path):
    origem = _arquivo(tmp_path, conteudo=b"conteudo exato")
    chave = result_storage.save(origem, size_bytes=1)
    caminho = result_storage.resolve_path(chave)
    assert isinstance(caminho, Path)
    assert caminho.read_bytes() == b"conteudo exato"


def test_resolve_path_fica_sempre_dentro_de_result_storage_dir(tmp_path):
    origem = _arquivo(tmp_path)
    chave = result_storage.save(origem, size_bytes=1)
    caminho = result_storage.resolve_path(chave)
    assert Path(result_storage.RESULT_STORAGE_DIR).resolve() in caminho.resolve().parents


def test_resolve_path_rejeita_chave_com_formato_invalido(tmp_path):
    for chave_ruim in ("../../etc/passwd", "/etc/passwd", "curta", "", "A" * 32):
        with pytest.raises(ValueError):
            result_storage.resolve_path(chave_ruim)


def test_resolve_path_nunca_aceita_caminho_absoluto_como_chave(tmp_path):
    with pytest.raises(ValueError):
        result_storage.resolve_path(str(tmp_path / "arquivo-qualquer.mp4"))


# ============================================================================ exists()
def test_exists_true_para_chave_existente(tmp_path):
    origem = _arquivo(tmp_path)
    chave = result_storage.save(origem, size_bytes=1)
    assert result_storage.exists(chave) is True


def test_exists_false_para_chave_inexistente(tmp_path):
    assert result_storage.exists("00000000000000000000000000000000") is False


def test_exists_false_quando_o_diretorio_de_storage_nem_existe_ainda(tmp_path):
    assert result_storage.exists("qualquer-chave") is False


# ============================================================================ delete()
def test_delete_remove_o_arquivo_de_verdade(tmp_path):
    origem = _arquivo(tmp_path)
    chave = result_storage.save(origem, size_bytes=1)
    result_storage.delete(chave)
    assert result_storage.exists(chave) is False


def test_delete_de_chave_inexistente_nao_levanta(tmp_path):
    result_storage.delete("chave-que-nunca-existiu")  # não levanta = passou


def test_delete_de_chave_inexistente_quando_diretorio_nao_existe_nao_levanta(tmp_path):
    result_storage.delete("qualquer-coisa")  # diretório nem foi criado ainda; não pode levantar


def test_delete_nao_afeta_outras_chaves(tmp_path):
    a = result_storage.save(_arquivo(tmp_path, "a.mp4"), size_bytes=1)
    b = result_storage.save(_arquivo(tmp_path, "b.mp4"), size_bytes=1)
    result_storage.delete(a)
    assert not result_storage.exists(a)
    assert result_storage.exists(b)


# ============================================================================ segurança (chave nunca é caminho)
def test_chave_com_caracteres_de_path_traversal_nao_escapa_o_diretorio(tmp_path):
    """exists()/delete() recebem uma string qualquer, mas a resolução chave->caminho exige o
    formato exato de uuid4().hex (32 hex) — qualquer outra coisa, incluindo "../", é rejeitada
    ANTES de virar caminho, então nunca escapa de RESULT_STORAGE_DIR."""
    fora_do_storage = tmp_path / "arquivo-fora.mp4"
    fora_do_storage.write_bytes(b"nao deveria ser acessivel")

    assert result_storage.exists("../arquivo-fora") is False
    result_storage.delete("../arquivo-fora")  # não deve apagar o arquivo de fora, e não deve levantar
    assert fora_do_storage.exists()


def test_chave_com_formato_invalido_e_sempre_rejeitada(tmp_path):
    """Só uuid4().hex (32 caracteres hexadecimais) é aceito — qualquer outra coisa é tratada como
    "não existe" (exists) ou "nada a fazer" (delete, sem levantar)."""
    invalidas = ["", "curta-demais", "A" * 32, "g" * 32, "1" * 31, "1" * 33, "../../etc/passwd"]
    for chave in invalidas:
        assert result_storage.exists(chave) is False, chave
        result_storage.delete(chave)  # não deve levantar para nenhuma delas


# ============================================================================ arquitetura
def test_result_storage_nao_importa_banco_rotas_processor_download_ou_fastapi():
    """result_storage.py é isolado: só sabe mexer em arquivos, nada de banco/HTTP/outras camadas."""
    tree = ast.parse(RESULT_STORAGE_FILE.read_text(encoding="utf-8"))
    raizes: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            raizes.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            raizes.add(node.module.split(".")[0])
    proibidos = {"database", "routes", "processor", "download", "fastapi", "starlette", "sqlalchemy", "main"}
    assert not (raizes & proibidos), raizes


def test_result_storage_interface_e_minima():
    """save/exists/delete (Etapa 8B.2) + resolve_path (Etapa 8B.3) -- nada de load/open/stream."""
    tree = ast.parse(RESULT_STORAGE_FILE.read_text(encoding="utf-8"))
    funcoes_publicas = {n.name for n in tree.body if isinstance(n, ast.FunctionDef) and not n.name.startswith("_")}
    assert funcoes_publicas == {"save", "exists", "delete", "resolve_path"}


def test_result_storage_nunca_usa_shell_ou_os_system():
    fonte = RESULT_STORAGE_FILE.read_text(encoding="utf-8")
    assert "shell=True" not in fonte and "os.system(" not in fonte
