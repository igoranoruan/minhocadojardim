"""GET /api/generations/{generation_id}/download: contrato HTTP, ownership, expiração e
segurança (Etapa 8B.3).

Correção da quota Free (aprovação do CÉREBRO): desde que get_generation_user passou a usar a
identidade Free do DISPOSITIVO para sessão sem plano pago, esta rota precisa considerar TAMBÉM a
conta autenticada como possível dona de uma geração (routes/deps.py::get_optional_authenticated_user
+ services/usage.py::get_downloadable_generation, parâmetro `authenticated_user_id`) -- senão uma
geração antiga de uma conta sem plano pago deixaria de ser baixável por ela mesma. O cookie Free em
si, sozinho, continua incapaz de baixar uma geração de outra conta e continua incapaz de conceder
acesso pago (ver os testes na seção "conta sem plano pago / cookie Free" abaixo)."""
from datetime import timedelta
from pathlib import Path

import pytest

from database.types import utcnow
from helpers_generation_flow import login_directly
from services import result_storage


@pytest.fixture(autouse=True)
def diretorio_de_storage(tmp_path, monkeypatch):
    monkeypatch.setattr("services.result_storage.RESULT_STORAGE_DIR", str(tmp_path / "results"))


def _salvar_resultado_real(conteudo: bytes = b"bytes do mp4 processado") -> str:
    """Cria um arquivo de verdade no storage (mesmo mecanismo real de save()) e devolve a chave."""
    import tempfile
    origem = Path(tempfile.mkdtemp()) / "saida.mp4"
    origem.write_bytes(conteudo)
    return result_storage.save(origem, size_bytes=len(conteudo))


def _url(generation_id: int) -> str:
    return f"/api/generations/{generation_id}/download"


# ============================================================================ sucesso
def test_sucesso_devolve_o_arquivo_com_os_headers_certos(auth_client, factory, session):
    usuario = factory.user()
    login_directly(auth_client, session, usuario)
    chave = _salvar_resultado_real(b"conteudo exato do mp4")
    geracao = factory.generation(
        user=usuario, status="completed",
        output_storage_key=chave, output_expires_at=utcnow() + timedelta(minutes=10),
    )

    resposta = auth_client.get(_url(geracao.id))

    assert resposta.status_code == 200
    assert resposta.content == b"conteudo exato do mp4"
    assert resposta.headers["content-type"] == "video/mp4"
    assert resposta.headers["content-disposition"] == f'attachment; filename="klango-{chave[:8]}.mp4"'
    assert resposta.headers["cache-control"] == "no-store"


def test_nome_do_arquivo_e_sempre_klango_storage_key_nunca_o_original(auth_client, factory, session):
    """Fallback (02/10/2026, pedido do Igor): klango-{8 chars de output_storage_key}.mp4 -- nunca
    o ID sequencial da geração (expunha volume de gerações do site) nem o nome do arquivo em disco."""
    usuario = factory.user()
    login_directly(auth_client, session, usuario)
    chave = _salvar_resultado_real()
    geracao = factory.generation(
        user=usuario, status="completed",
        output_storage_key=chave, output_expires_at=utcnow() + timedelta(minutes=10),
    )
    resposta = auth_client.get(_url(geracao.id))
    assert f"klango-{chave[:8]}.mp4" in resposta.headers["content-disposition"]
    assert "saida.mp4" not in resposta.headers["content-disposition"]  # nunca o nome do arquivo em disco
    assert f"minhoca-{geracao.id}" not in resposta.headers["content-disposition"]  # nunca o padrão antigo


# ============================================================================ autenticação / Free anônimo
def test_sem_sessao_cria_identidade_anonima_e_devolve_404_generico(auth_client):
    """Free anônimo (aprovação do CÉREBRO): a rota usa get_generation_user, não get_current_user
    -- sem sessão, ganha uma identidade anônima automaticamente (nunca 401); como a geração #1
    não existe (e não pertence a essa identidade recém-criada), o 404 genérico de sempre continua
    valendo -- ownership por user_id funciona igual para identidade anônima."""
    resposta = auth_client.get(_url(1))
    assert resposta.status_code == 404
    assert resposta.headers["set-cookie"].startswith("minhoca_anon=")


# ============================================================================ autorização / IDOR
def test_geracao_de_outro_usuario_404_generico(auth_client, factory, session):
    dono = factory.user()
    atacante = factory.user()
    login_directly(auth_client, session, atacante)  # autenticado como o ATACANTE
    chave = _salvar_resultado_real()
    geracao_do_dono = factory.generation(
        user=dono, status="completed",
        output_storage_key=chave, output_expires_at=utcnow() + timedelta(minutes=10),
    )

    resposta = auth_client.get(_url(geracao_do_dono.id))
    assert resposta.status_code == 404


def test_geracao_inexistente_404_generico(auth_client, factory, session):
    login_directly(auth_client, session, factory.user())
    resposta = auth_client.get(_url(999999))
    assert resposta.status_code == 404


def test_erro_de_outro_usuario_e_de_inexistente_tem_o_mesmo_corpo(auth_client, factory, session):
    """A resposta não pode dar nenhuma pista que diferencie os dois casos."""
    dono = factory.user()
    atacante = factory.user()
    chave = _salvar_resultado_real()
    geracao_do_dono = factory.generation(
        user=dono, status="completed",
        output_storage_key=chave, output_expires_at=utcnow() + timedelta(minutes=10),
    )

    login_directly(auth_client, session, atacante)
    resposta_outro_usuario = auth_client.get(_url(geracao_do_dono.id))
    resposta_inexistente = auth_client.get(_url(999999))

    assert resposta_outro_usuario.status_code == resposta_inexistente.status_code == 404
    assert resposta_outro_usuario.json() == resposta_inexistente.json()


# ============================================================================ status
def test_status_reserved_404(auth_client, factory, session):
    usuario = factory.user()
    login_directly(auth_client, session, usuario)
    geracao = factory.generation(user=usuario, status="reserved")
    resposta = auth_client.get(_url(geracao.id))
    assert resposta.status_code == 404


def test_status_failed_404(auth_client, factory, session):
    usuario = factory.user()
    login_directly(auth_client, session, usuario)
    geracao = factory.generation(user=usuario, status="failed", error_code="DownloadFailedError")
    resposta = auth_client.get(_url(geracao.id))
    assert resposta.status_code == 404


# ============================================================================ expiração
def test_output_expires_at_no_passado_404(auth_client, factory, session):
    usuario = factory.user()
    login_directly(auth_client, session, usuario)
    chave = _salvar_resultado_real()
    geracao = factory.generation(
        user=usuario, status="completed",
        output_storage_key=chave, output_expires_at=utcnow() - timedelta(seconds=1),
    )
    resposta = auth_client.get(_url(geracao.id))
    assert resposta.status_code == 404


def test_output_expires_at_no_futuro_ainda_baixavel(auth_client, factory, session):
    usuario = factory.user()
    login_directly(auth_client, session, usuario)
    chave = _salvar_resultado_real()
    geracao = factory.generation(
        user=usuario, status="completed",
        output_storage_key=chave, output_expires_at=utcnow() + timedelta(seconds=5),
    )
    resposta = auth_client.get(_url(geracao.id))
    assert resposta.status_code == 200


# ============================================================================ storage
def test_output_storage_key_ausente_404(auth_client, factory, session):
    """Geração completed mas sem storage_key (ex.: de antes da Etapa 8B.1/8B.2)."""
    usuario = factory.user()
    login_directly(auth_client, session, usuario)
    geracao = factory.generation(
        user=usuario, status="completed",
        output_sha256="a" * 64,  # tem resultado "antigo", mas nunca teve storage_key
    )
    resposta = auth_client.get(_url(geracao.id))
    assert resposta.status_code == 404


def test_arquivo_fisico_ausente_404_nao_500(auth_client, factory, session):
    """Banco diz que existe (completed, storage_key válida, não expirada), mas o arquivo físico
    não está lá -- deve virar 404 igual aos outros casos, nunca um 500."""
    usuario = factory.user()
    login_directly(auth_client, session, usuario)
    geracao = factory.generation(
        user=usuario, status="completed",
        output_storage_key="f" * 32,  # chave com formato válido, mas nada foi salvo com ela
        output_expires_at=utcnow() + timedelta(minutes=10),
    )
    resposta = auth_client.get(_url(geracao.id))
    assert resposta.status_code == 404


# ============================================================================ segurança / não vazamento
def test_storage_key_nunca_aparece_em_nenhuma_resposta(auth_client, factory, session):
    usuario = factory.user()
    login_directly(auth_client, session, usuario)
    chave = _salvar_resultado_real()
    geracao = factory.generation(
        user=usuario, status="completed",
        output_storage_key=chave, output_expires_at=utcnow() + timedelta(minutes=10),
    )

    resposta_sucesso = auth_client.get(_url(geracao.id))
    assert chave not in str(resposta_sucesso.headers)

    resposta_erro = auth_client.get(_url(999999))
    assert chave not in resposta_erro.text


def test_result_storage_dir_nunca_aparece_em_nenhuma_resposta(auth_client, factory, session):
    usuario = factory.user()
    login_directly(auth_client, session, usuario)
    chave = _salvar_resultado_real()
    geracao = factory.generation(
        user=usuario, status="completed",
        output_storage_key=chave, output_expires_at=utcnow() + timedelta(minutes=10),
    )
    resposta = auth_client.get(_url(geracao.id))
    assert "tmp" not in str(resposta.headers).lower() or "tmp_path" not in str(resposta.headers)
    assert str(result_storage.RESULT_STORAGE_DIR) not in str(resposta.headers)


def test_resposta_de_erro_segue_o_formato_padrao(auth_client, factory, session):
    login_directly(auth_client, session, factory.user())
    resposta = auth_client.get(_url(999999))
    assert set(resposta.json()) == {"detail", "code"}
    assert resposta.headers["cache-control"] == "no-store"


# ============================================================================ conta sem plano pago / cookie Free
def test_conta_autenticada_sem_plano_pago_ainda_baixa_geracao_da_propria_conta(auth_client, factory, session):
    """Correção da quota Free (aprovação do CÉREBRO): a identidade EFETIVA de uma sessão sem plano
    pago passou a ser a do dispositivo -- mas uma geração que pertence à própria CONTA autenticada
    (ex.: de antes desta correção, ou de quando o plano ainda era pago) continua baixável por ela."""
    usuario = factory.user()
    login_directly(auth_client, session, usuario)  # SEM plano pago
    chave = _salvar_resultado_real()
    geracao = factory.generation(
        user=usuario, status="completed",
        output_storage_key=chave, output_expires_at=utcnow() + timedelta(minutes=10),
    )
    resposta = auth_client.get(_url(geracao.id))
    assert resposta.status_code == 200
    assert resposta.content == b"bytes do mp4 processado"


def test_cookie_free_sozinho_nao_baixa_geracao_de_conta_paga_alheia(auth_client, factory, session):
    """O cookie Free nunca pode virar sessão nem conceder acesso pago: um visitante SEM sessão
    (só a identidade Free do dispositivo, criada automaticamente) nunca baixa a geração de uma
    conta paga alheia, mesmo conhecendo o id."""
    dono_pago = factory.user()
    chave = _salvar_resultado_real()
    geracao = factory.generation(
        user=dono_pago, status="completed",
        output_storage_key=chave, output_expires_at=utcnow() + timedelta(minutes=10),
    )
    resposta = auth_client.get(_url(geracao.id))  # SEM login -- só a identidade Free automática
    assert resposta.status_code == 404
    assert resposta.headers["set-cookie"].startswith("minhoca_anon=")
