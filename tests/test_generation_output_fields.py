"""Schema dos campos de resultado em `generations` (Etapa 8B.1): output_size_bytes,
output_expires_at, output_storage_key.

Só schema: nada aqui testa storage, endpoint de download, generation_flow ou limpeza — isso é
Etapa 8B.2 em diante. O objetivo é só garantir que a migration 0003 e o modelo estão certos e que
gerações já existentes (sem esses campos) continuam válidas depois da migração.
"""
from datetime import timedelta

import pytest
from alembic import command
from sqlalchemy import inspect, text

from database.types import utcnow


# ============================================================================ existência/tipos/nullable
def test_as_tres_colunas_existem_com_o_tipo_e_a_nulabilidade_esperados(engine):
    colunas = {c["name"]: c for c in inspect(engine).get_columns("generations")}

    assert "output_size_bytes" in colunas
    assert colunas["output_size_bytes"]["nullable"] is True
    assert colunas["output_size_bytes"]["type"].__class__.__name__ == "INTEGER"

    assert "output_expires_at" in colunas
    assert colunas["output_expires_at"]["nullable"] is True
    assert "DATETIME" in colunas["output_expires_at"]["type"].__class__.__name__.upper()

    assert "output_storage_key" in colunas
    assert colunas["output_storage_key"]["nullable"] is True
    tipo_key = colunas["output_storage_key"]["type"]
    assert tipo_key.__class__.__name__ == "VARCHAR"
    assert tipo_key.length == 255


def test_nenhum_campo_proibido_foi_adicionado(engine):
    """A autorização foi explícita: sem filename, mime_type ou output_path (caminho físico)."""
    colunas = {c["name"] for c in inspect(engine).get_columns("generations")}
    assert not colunas & {"filename", "mime_type", "output_path"}


# ============================================================================ migration aplicável / banco sobe
def test_migration_0003_e_o_head_atual(db_url, alembic_cfg):
    command.upgrade(alembic_cfg, "head")
    from database.session import create_db_engine

    eng = create_db_engine(db_url)
    try:
        with eng.connect() as conn:
            assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0003"
    finally:
        eng.dispose()


def test_banco_de_desenvolvimento_sobe_do_zero_ate_o_head_com_a_0003(db_url, alembic_cfg):
    """alembic upgrade head, partindo de um banco vazio, precisa passar por 0001 -> 0002 -> 0003
    sem erro -- é exatamente o que roda num ambiente novo (dev local ou primeiro deploy)."""
    command.upgrade(alembic_cfg, "head")  # não levanta = passou


def test_alembic_check_sem_diferenca_entre_modelo_e_migration(db_url, alembic_cfg):
    command.upgrade(alembic_cfg, "head")
    command.check(alembic_cfg)  # levanta se o modelo e a migration divergirem


def test_0003_downgrade_remove_so_os_tres_campos_novos(db_url, alembic_cfg):
    """0003 é aditiva: subir e descer só ela deve ir e voltar sem afetar 0001/0002."""
    from database.session import create_db_engine

    command.upgrade(alembic_cfg, "0002")
    eng = create_db_engine(db_url)
    try:
        colunas_antes = {c["name"] for c in inspect(eng).get_columns("generations")}
    finally:
        eng.dispose()
    assert not colunas_antes & {"output_size_bytes", "output_expires_at", "output_storage_key"}

    command.upgrade(alembic_cfg, "0003")
    eng = create_db_engine(db_url)
    try:
        colunas_depois = {c["name"] for c in inspect(eng).get_columns("generations")}
    finally:
        eng.dispose()
    assert colunas_depois == colunas_antes | {"output_size_bytes", "output_expires_at", "output_storage_key"}

    command.downgrade(alembic_cfg, "0002")
    eng = create_db_engine(db_url)
    try:
        colunas_volta = {c["name"] for c in inspect(eng).get_columns("generations")}
    finally:
        eng.dispose()
    assert colunas_volta == colunas_antes  # voltou exatamente ao que era antes da 0003


def test_0001_e_0002_continuam_funcionando_com_a_0003_no_topo(db_url, alembic_cfg):
    """As migrations antigas não foram alteradas: upgrade passo a passo até 0003 continua ok."""
    command.upgrade(alembic_cfg, "0001")
    command.upgrade(alembic_cfg, "0002")
    command.upgrade(alembic_cfg, "0003")  # não levanta = as três continuam compatíveis entre si


# ============================================================================ geração antiga sem resultado
def test_geracao_sem_resultado_e_valida_os_tres_campos_ficam_null(factory, session):
    """Geração comum (como qualquer uma criada antes desta etapa): os 3 campos novos não são
    informados e ficam NULL -- nenhuma geração antiga precisa ser tocada/reconstruída."""
    geracao = factory.generation(status="completed", output_sha256="a" * 64)
    session.refresh(geracao)
    assert geracao.output_size_bytes is None
    assert geracao.output_expires_at is None
    assert geracao.output_storage_key is None
    assert geracao.output_sha256 == "a" * 64  # os campos antigos continuam funcionando normalmente


def test_geracao_pode_gravar_os_tres_campos_novos(factory, session):
    agora = utcnow()
    geracao = factory.generation(
        status="completed",
        output_sha256="b" * 64,
        output_size_bytes=1234,
        output_expires_at=agora + timedelta(minutes=30),
        output_storage_key="chave-abstrata-de-exemplo",
    )
    session.refresh(geracao)
    assert geracao.output_size_bytes == 1234
    assert geracao.output_expires_at == agora + timedelta(minutes=30)
    assert geracao.output_storage_key == "chave-abstrata-de-exemplo"


# ============================================================================ CHECK do tamanho
def test_output_size_bytes_negativo_e_rejeitado(factory, assert_rejected):
    assert_rejected(lambda: factory.generation(output_size_bytes=-1))


def test_output_size_bytes_zero_e_positivo_sao_aceitos(factory):
    factory.generation(output_size_bytes=0)
    factory.generation(output_size_bytes=999)


def test_output_size_bytes_null_e_aceito(factory, session):
    geracao = factory.generation(output_size_bytes=None)
    session.refresh(geracao)
    assert geracao.output_size_bytes is None


# ============================================================================ regras antigas continuam valendo
def test_constraints_antigas_de_generations_continuam_valendo(factory, assert_rejected):
    """Prova de que a 0003 não afetou nenhuma regra pré-existente (ex.: status inválido)."""
    assert_rejected(lambda: factory.generation(status="isso-nao-existe"))
