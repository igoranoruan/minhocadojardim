"""Arquivo temporário: nome seguro (nunca derivado de dado externo) e limpeza garantida."""
import re

from download.tempfiles import cleanup, new_temp_stub

UUID_RE = re.compile(r"^[0-9a-f]{32}$")


def test_stub_tem_nome_aleatorio_sem_extensao():
    stub = new_temp_stub()
    assert UUID_RE.fullmatch(stub.name)
    assert stub.suffix == ""


def test_stubs_nunca_se_repetem():
    stubs = {new_temp_stub().name for _ in range(200)}
    assert len(stubs) == 200


def test_diretorio_e_criado_automaticamente(tmp_path, monkeypatch):
    destino = tmp_path / "nao-existe-ainda" / "downloads"
    monkeypatch.setattr("download.tempfiles.DOWNLOAD_TEMP_DIR", str(destino))
    stub = new_temp_stub()
    assert destino.is_dir()
    assert stub.parent == destino


def test_nome_nunca_reflete_dado_externo():
    # Nada aqui usa plataforma, título ou URL: só uuid4. Garantimos isso testando que o nome
    # não contém substrings óbvias que apareceriam se algo externo vazasse para o nome do arquivo.
    stub = new_temp_stub()
    for suspeito in ("tiktok", "instagram", "youtube", "pinterest", "http", "..", "/"):
        assert suspeito not in stub.name


def test_cleanup_remove_o_arquivo_final_e_os_parciais(tmp_path, monkeypatch):
    monkeypatch.setattr("download.tempfiles.DOWNLOAD_TEMP_DIR", str(tmp_path))
    stub = new_temp_stub()
    final = stub.with_suffix(".mp4")
    parcial = stub.with_suffix(".mp4.part")
    outro = stub.with_suffix(".ytdl")
    for arquivo in (final, parcial, outro):
        arquivo.write_bytes(b"x")

    removidos = cleanup(stub)

    assert removidos == 3
    assert not final.exists() and not parcial.exists() and not outro.exists()


def test_cleanup_sem_nenhum_arquivo_nao_falha(tmp_path, monkeypatch):
    monkeypatch.setattr("download.tempfiles.DOWNLOAD_TEMP_DIR", str(tmp_path))
    stub = new_temp_stub()
    assert cleanup(stub) == 0  # nada a remover: não levanta exceção


def test_cleanup_nao_afeta_arquivos_de_outro_stub(tmp_path, monkeypatch):
    monkeypatch.setattr("download.tempfiles.DOWNLOAD_TEMP_DIR", str(tmp_path))
    meu = new_temp_stub()
    alheio = new_temp_stub()
    meu.with_suffix(".mp4").write_bytes(b"x")
    alheio.with_suffix(".mp4").write_bytes(b"y")

    cleanup(meu)

    assert not meu.with_suffix(".mp4").exists()
    assert alheio.with_suffix(".mp4").exists()
