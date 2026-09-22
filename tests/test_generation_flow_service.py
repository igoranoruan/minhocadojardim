"""services/generation_flow.py: orquestração da geração individual (dublês de download/processor;
services.usage é o real, com banco de teste — a cota é conferida de verdade)."""
from pathlib import Path
from unittest.mock import patch

import pytest

from database.models import Generation
from download.errors import DownloadFailedError, InvalidUrlError, UnsupportedPlatformError
from download.platform import Platform
from helpers_generation_flow import fake_download_result, fake_processing_result
from processor.errors import FfmpegFailedError
from services.generation_flow import GenerationPersistenceError, generate_from_url
from services.usage import QuotaExceededError, complete_generation, get_allowance, reserve_generation


def _files(tmp_path, nome_download="baixado.mp4", nome_saida="saida.mp4"):
    download_path = tmp_path / nome_download
    download_path.write_bytes(b"video baixado")
    output_path = tmp_path / nome_saida
    output_path.write_bytes(b"video processado")
    return download_path, output_path


# ============================================================================ sucesso
def test_sucesso_completo_grava_completed_e_devolve_metadados(factory, session, tmp_path):
    usuario = factory.user()
    download_path, output_path = _files(tmp_path)

    with patch(
        "services.generation_flow.download_video",
        return_value=fake_download_result(download_path, platform=Platform.TIKTOK),
    ), patch(
        "services.generation_flow.process_video",
        return_value=fake_processing_result(output_path, sha="b" * 64, duration=7.5, size=555),
    ):
        resultado = generate_from_url(session, user=usuario, url="https://www.tiktok.com/@a/video/1")

    assert resultado.status == "completed"
    assert resultado.platform == "tiktok"
    assert resultado.output_sha256 == "b" * 64
    assert resultado.size_bytes == 555
    assert resultado.duration_seconds == 7.5

    session.expire_all()
    linha = session.get(Generation, resultado.generation_id)
    assert linha.status == "completed"
    assert linha.output_sha256 == "b" * 64
    assert linha.duration_ms == 7500  # round(7.5 * 1000)
    assert linha.user_id == usuario.id
    assert linha.plan_code == "free"


def test_limpeza_dos_arquivos_temporarios_em_sucesso(factory, session, tmp_path):
    usuario = factory.user()
    download_path, output_path = _files(tmp_path)
    with patch("services.generation_flow.download_video", return_value=fake_download_result(download_path)), \
         patch("services.generation_flow.process_video", return_value=fake_processing_result(output_path)):
        generate_from_url(session, user=usuario, url="https://www.tiktok.com/@a/video/1")
    assert not download_path.exists()
    assert not output_path.exists()


# ============================================================================ validação antes da reserva
def test_url_invalida_nao_reserva_geracao(factory, session):
    usuario = factory.user()
    antes = get_allowance(session, usuario.id).remaining
    with pytest.raises(InvalidUrlError):
        generate_from_url(session, user=usuario, url="ftp://exemplo.com/x")
    assert get_allowance(session, usuario.id).remaining == antes
    assert session.query(Generation).filter_by(user_id=usuario.id).count() == 0


def test_plataforma_nao_suportada_nao_reserva_geracao(factory, session):
    usuario = factory.user()
    antes = get_allowance(session, usuario.id).remaining
    with pytest.raises(UnsupportedPlatformError):
        generate_from_url(session, user=usuario, url="https://example.com/video")
    assert get_allowance(session, usuario.id).remaining == antes
    assert session.query(Generation).filter_by(user_id=usuario.id).count() == 0


# ============================================================================ cota
def test_cota_esgotada_nao_chama_download(factory, session):
    usuario = factory.user()
    for i in range(5):  # esgota o Free (5/semana) usando o serviço real
        reserve_generation(session, user_id=usuario.id, request_id=f"pre-{i}")

    with patch("services.generation_flow.download_video") as mock_download:
        with pytest.raises(QuotaExceededError):
            generate_from_url(session, user=usuario, url="https://www.tiktok.com/@a/video/1")
    mock_download.assert_not_called()
    assert session.query(Generation).filter_by(user_id=usuario.id).count() == 5  # nenhuma a mais


# ============================================================================ falha após reserva
def test_download_falha_marca_failed_e_libera_a_cota(factory, session):
    usuario = factory.user()
    antes = get_allowance(session, usuario.id).remaining

    with patch("services.generation_flow.download_video", side_effect=DownloadFailedError("falha")):
        with pytest.raises(DownloadFailedError):
            generate_from_url(session, user=usuario, url="https://www.tiktok.com/@a/video/1")

    session.expire_all()
    (linha,) = session.query(Generation).filter_by(user_id=usuario.id).all()
    assert linha.status == "failed"
    assert linha.error_code == "DownloadFailedError"
    assert get_allowance(session, usuario.id).remaining == antes  # não ficou consumida


def test_processamento_falha_marca_failed_e_libera_a_cota(factory, session, tmp_path):
    usuario = factory.user()
    antes = get_allowance(session, usuario.id).remaining
    download_path, _ = _files(tmp_path)

    with patch("services.generation_flow.download_video", return_value=fake_download_result(download_path)), \
         patch("services.generation_flow.process_video", side_effect=FfmpegFailedError("falha")):
        with pytest.raises(FfmpegFailedError):
            generate_from_url(session, user=usuario, url="https://www.tiktok.com/@a/video/1")

    session.expire_all()
    (linha,) = session.query(Generation).filter_by(user_id=usuario.id).all()
    assert linha.status == "failed" and linha.error_code == "FfmpegFailedError"
    assert get_allowance(session, usuario.id).remaining == antes
    assert not download_path.exists()  # o temporário de download foi limpo mesmo com falha


def test_excecao_inesperada_apos_reserva_tambem_marca_failed(factory, session, tmp_path):
    usuario = factory.user()
    antes = get_allowance(session, usuario.id).remaining
    download_path, _ = _files(tmp_path)

    with patch("services.generation_flow.download_video", return_value=fake_download_result(download_path)), \
         patch("services.generation_flow.process_video", side_effect=RuntimeError("bug interno")):
        with pytest.raises(RuntimeError):
            generate_from_url(session, user=usuario, url="https://www.tiktok.com/@a/video/1")

    session.expire_all()
    (linha,) = session.query(Generation).filter_by(user_id=usuario.id).all()
    assert linha.status == "failed" and linha.error_code == "RuntimeError"
    assert get_allowance(session, usuario.id).remaining == antes


# ============================================================================ falha de persistência
def test_falha_ao_gravar_complete_generation_nao_mascara_como_erro_de_video(factory, session, tmp_path):
    usuario = factory.user()
    download_path, output_path = _files(tmp_path)

    with patch("services.generation_flow.download_video", return_value=fake_download_result(download_path)), \
         patch("services.generation_flow.process_video", return_value=fake_processing_result(output_path)), \
         patch("services.generation_flow.complete_generation", side_effect=RuntimeError("banco fora do ar")):
        with pytest.raises(GenerationPersistenceError) as erro:
            generate_from_url(session, user=usuario, url="https://www.tiktok.com/@a/video/1")

    assert erro.value.phase == "complete"
    assert not download_path.exists() and not output_path.exists()  # limpeza acontece mesmo assim


def test_falha_ao_gravar_fail_generation_preserva_a_causa_original(factory, session):
    usuario = factory.user()
    with patch("services.generation_flow.download_video", side_effect=DownloadFailedError("x")), \
         patch("services.generation_flow.fail_generation", side_effect=RuntimeError("banco fora do ar")):
        with pytest.raises(GenerationPersistenceError) as erro:
            generate_from_url(session, user=usuario, url="https://www.tiktok.com/@a/video/1")
    assert erro.value.phase == "fail"
    assert isinstance(erro.value.__cause__, DownloadFailedError)


# ============================================================================ isolamento entre usuários
def test_duas_geracoes_de_usuarios_diferentes_nao_se_misturam(factory, session, tmp_path):
    usuario_a, usuario_b = factory.user(), factory.user()
    download_a, output_a = _files(tmp_path, "a-down.mp4", "a-saida.mp4")
    download_b, output_b = _files(tmp_path, "b-down.mp4", "b-saida.mp4")

    with patch("services.generation_flow.download_video", return_value=fake_download_result(download_a)), \
         patch("services.generation_flow.process_video", return_value=fake_processing_result(output_a, sha="1" * 64)):
        resultado_a = generate_from_url(session, user=usuario_a, url="https://www.tiktok.com/@a/video/1")

    with patch("services.generation_flow.download_video", return_value=fake_download_result(download_b)), \
         patch("services.generation_flow.process_video", return_value=fake_processing_result(output_b, sha="2" * 64)):
        resultado_b = generate_from_url(session, user=usuario_b, url="https://www.tiktok.com/@a/video/1")

    session.expire_all()
    linha_a = session.get(Generation, resultado_a.generation_id)
    linha_b = session.get(Generation, resultado_b.generation_id)
    assert linha_a.user_id == usuario_a.id and linha_b.user_id == usuario_b.id
    assert linha_a.output_sha256 == "1" * 64 and linha_b.output_sha256 == "2" * 64
    assert get_allowance(session, usuario_a.id).used == 1
    assert get_allowance(session, usuario_b.id).used == 1  # o consumo de A não afetou B
