"""Arquivo temporário de saída do processor: nome seguro e limpeza garantida (mesmo padrão de
download/tempfiles.py, testado na Etapa 5)."""
import re

from processor.tempfiles import cleanup, new_temp_stub

UUID_RE = re.compile(r"^[0-9a-f]{32}$")


def test_stub_tem_nome_aleatorio_sem_extensao():
    stub = new_temp_stub()
    assert UUID_RE.fullmatch(stub.name)
    assert stub.suffix == ""


def test_stubs_nunca_se_repetem():
    assert len({new_temp_stub().name for _ in range(200)}) == 200


def test_diretorio_e_criado_automaticamente(tmp_path, monkeypatch):
    destino = tmp_path / "nao-existe" / "processing"
    monkeypatch.setattr("processor.tempfiles.PROCESSING_TEMP_DIR", str(destino))
    stub = new_temp_stub()
    assert destino.is_dir() and stub.parent == destino


def test_nome_nunca_reflete_dado_externo():
    stub = new_temp_stub()
    for suspeito in ("mp4", "ffmpeg", "input", "output", "/", ".."):
        assert suspeito not in stub.name


def test_cleanup_remove_o_arquivo_final_e_qualquer_parcial(tmp_path, monkeypatch):
    monkeypatch.setattr("processor.tempfiles.PROCESSING_TEMP_DIR", str(tmp_path))
    stub = new_temp_stub()
    for suf in (".mp4", ".mp4.tmp"):
        stub.with_suffix(suf).write_bytes(b"x")
    assert cleanup(stub) == 2
    assert not stub.with_suffix(".mp4").exists() and not stub.with_suffix(".mp4.tmp").exists()


def test_cleanup_sem_nenhum_arquivo_nao_falha(tmp_path, monkeypatch):
    monkeypatch.setattr("processor.tempfiles.PROCESSING_TEMP_DIR", str(tmp_path))
    assert cleanup(new_temp_stub()) == 0


def test_cleanup_nao_afeta_outro_stub(tmp_path, monkeypatch):
    monkeypatch.setattr("processor.tempfiles.PROCESSING_TEMP_DIR", str(tmp_path))
    meu, alheio = new_temp_stub(), new_temp_stub()
    meu.with_suffix(".mp4").write_bytes(b"x")
    alheio.with_suffix(".mp4").write_bytes(b"y")
    cleanup(meu)
    assert not meu.with_suffix(".mp4").exists()
    assert alheio.with_suffix(".mp4").exists()
