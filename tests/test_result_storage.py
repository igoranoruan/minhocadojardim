"""services/result_storage.py: save/exists/delete (Etapa 8B.2). Puro filesystem, sem banco."""
import ast
import os
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
    # sweep_expired (02/11/2026, pedido e aprovação do Igor): a expiração por TTL que a 8B.4 previa.
    assert funcoes_publicas == {"save", "exists", "delete", "resolve_path", "sweep_expired"}


def test_result_storage_nunca_usa_shell_ou_os_system():
    fonte = RESULT_STORAGE_FILE.read_text(encoding="utf-8")
    assert "shell=True" not in fonte and "os.system(" not in fonte


# ============================================================================ extensão "mp3" (11/10/2026, extração de áudio -- autorização do CÉREBRO)
def test_save_e_resolve_path_aceitam_extensao_mp3(tmp_path):
    """services/audio_flow.py salva o MP3 final com extension="mp3" -- mesmo padrão já usado para
    "jpg" (imagem, Etapa 03/10/2026); sem isso, save() levantaria ValueError para todo áudio."""
    origem = _arquivo(tmp_path, nome="origem.mp3", conteudo=b"bytes do mp3 processado")
    chave = result_storage.save(origem, size_bytes=1, extension="mp3")
    assert result_storage.exists(chave, extension="mp3") is True
    caminho = result_storage.resolve_path(chave, extension="mp3")
    assert caminho.name == f"{chave}.mp3"
    assert caminho.read_bytes() == b"bytes do mp3 processado"


# ============================================================================ sweep_expired() (02/11/2026, pedido e aprovação do Igor)
def _salvo_com_idade(tmp_path, idade_segundos, *, extension="mp4", agora=1_000_000.0, nome="origem"):
    origem = _arquivo(tmp_path, nome=f"{nome}.{extension}", conteudo=b"x" * 10)
    chave = result_storage.save(origem, size_bytes=10, extension=extension)
    caminho = Path(result_storage.RESULT_STORAGE_DIR) / f"{chave}.{extension}"
    os.utime(caminho, (agora - idade_segundos, agora - idade_segundos))
    return chave


def test_sweep_apaga_so_os_vencidos(tmp_path):
    agora = 1_000_000.0
    velho = _salvo_com_idade(tmp_path, 3601, agora=agora, nome="a")
    novo = _salvo_com_idade(tmp_path, 60, agora=agora, nome="b")
    assert result_storage.sweep_expired(3600, now=agora) == (1, 10)
    assert not result_storage.exists(velho)
    assert result_storage.exists(novo)


def test_sweep_cobre_video_imagem_e_audio(tmp_path):
    agora = 1_000_000.0
    chaves = [
        (_salvo_com_idade(tmp_path, 7200, extension=ext, agora=agora, nome=f"f{i}"), ext)
        for i, ext in enumerate(["mp4", "jpg", "mp3", "zip"])
    ]
    assert result_storage.sweep_expired(3600, now=agora)[0] == 4
    assert not any(result_storage.exists(chave, ext) for chave, ext in chaves)


def test_sweep_ignora_arquivos_que_nao_foram_gerados_pelo_save(tmp_path):
    agora = 1_000_000.0
    diretorio = Path(result_storage.RESULT_STORAGE_DIR)
    diretorio.mkdir(parents=True, exist_ok=True)
    estranhos = [diretorio / "leia-me.txt", diretorio / ("a" * 32 + ".exe"), diretorio / "nao-e-chave.mp4"]
    for arquivo in estranhos:
        arquivo.write_bytes(b"x")
        os.utime(arquivo, (agora - 99999, agora - 99999))
    assert result_storage.sweep_expired(60, now=agora) == (0, 0)
    assert all(arquivo.exists() for arquivo in estranhos)


def test_sweep_sem_diretorio_nao_falha():
    assert result_storage.sweep_expired(60) == (0, 0)


def test_sweep_no_limite_exato_nao_apaga(tmp_path):
    """Idade igual ao limite ainda não venceu (só apaga o que PASSOU do limite)."""
    agora = 1_000_000.0
    chave = _salvo_com_idade(tmp_path, 3600, agora=agora)
    assert result_storage.sweep_expired(3600, now=agora) == (0, 0)
    assert result_storage.exists(chave)

