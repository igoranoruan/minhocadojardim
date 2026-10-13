"""services/audio_flow.py: orquestração da extração de áudio avulsa (dublês de download/processor;
services.audio_usage é o real, com banco de teste -- o limite diário é conferido de verdade).
Mesmo padrão de tests/test_generation_flow_service.py."""
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from database.models import AudioExtraction, Generation
from download.errors import DownloadFailedError, InvalidUrlError, UnsupportedPlatformError
from download.platform import Platform
from helpers_audio import fake_audio_download_result, fake_audio_processing_result
from helpers_usage import give
from processor.errors import FfmpegFailedError, NoAudioStreamError
from services import result_storage
from services.audio_flow import AudioPersistenceError, extract_audio_from_url
from services.audio_usage import AudioNotAllowedError, AudioQuotaExceededError, get_audio_allowance
from utils.time_sp import now_sp


@pytest.fixture(autouse=True)
def diretorio_de_storage(tmp_path, monkeypatch):
    monkeypatch.setattr("services.result_storage.RESULT_STORAGE_DIR", str(tmp_path / "results"))


def _usuario_pago(factory, session, plan_code="weekly"):
    """Concede o plano com o relógio REAL (now_sp()), nunca o NOW congelado de helpers_usage --
    extract_audio_from_url (services/audio_flow.py) não recebe `now` injetado (não existe esse
    parâmetro na API), então sempre confere contra a hora real; um entitlement concedido numa
    data passada fixa ficaria expirado de verdade assim que o relógio real passasse da janela do
    plano (ex.: 7 dias do Semanal), quebrando o teste sem nenhuma mudança de comportamento real."""
    usuario = factory.user()
    give(factory, session, usuario, plan_code, now=now_sp())
    return usuario


def _files(tmp_path, nome_download="fonte.m4a", nome_saida="saida.mp3"):
    download_path = tmp_path / nome_download
    download_path.write_bytes(b"fonte de audio baixada")
    output_path = tmp_path / nome_saida
    output_path.write_bytes(b"mp3 processado")
    return download_path, output_path


# ============================================================================ sucesso
def test_sucesso_completo_grava_completed_e_devolve_metadados(factory, session, tmp_path):
    usuario = _usuario_pago(factory, session)
    download_path, output_path = _files(tmp_path)

    with patch(
        "services.audio_flow.download_audio_source",
        return_value=fake_audio_download_result(download_path, platform=Platform.TIKTOK),
    ), patch(
        "services.audio_flow.process_audio",
        return_value=fake_audio_processing_result(output_path, sha="b" * 64, duration=7.5, size=555),
    ):
        resultado = extract_audio_from_url(session, user=usuario, url="https://www.tiktok.com/@a/video/1")

    assert resultado.status == "completed"
    assert resultado.platform == "tiktok"
    assert resultado.output_sha256 == "b" * 64
    assert resultado.size_bytes == 555
    assert resultado.duration_seconds == 7.5

    session.expire_all()
    linha = session.get(AudioExtraction, resultado.extraction_id)
    assert linha.status == "completed"
    assert linha.output_sha256 == "b" * 64
    assert linha.duration_ms == 7500
    assert linha.user_id == usuario.id


def test_limpeza_dos_arquivos_temporarios_em_sucesso(factory, session, tmp_path):
    usuario = _usuario_pago(factory, session)
    download_path, output_path = _files(tmp_path)
    with patch("services.audio_flow.download_audio_source", return_value=fake_audio_download_result(download_path)), \
         patch("services.audio_flow.process_audio", return_value=fake_audio_processing_result(output_path)):
        extract_audio_from_url(session, user=usuario, url="https://www.tiktok.com/@a/video/1")
    assert not download_path.exists()
    assert not output_path.exists()


def test_nunca_cria_linha_em_generations(factory, session, tmp_path):
    usuario = _usuario_pago(factory, session)
    download_path, output_path = _files(tmp_path)
    with patch("services.audio_flow.download_audio_source", return_value=fake_audio_download_result(download_path)), \
         patch("services.audio_flow.process_audio", return_value=fake_audio_processing_result(output_path)):
        extract_audio_from_url(session, user=usuario, url="https://www.tiktok.com/@a/video/1")
    assert session.query(Generation).count() == 0


# ============================================================================ validação antes da reserva
def test_url_invalida_nao_reserva_extracao(factory, session):
    usuario = _usuario_pago(factory, session)
    with pytest.raises(InvalidUrlError):
        extract_audio_from_url(session, user=usuario, url="ftp://exemplo.com/x")
    assert session.query(AudioExtraction).filter_by(user_id=usuario.id).count() == 0


def test_plataforma_nao_suportada_nao_reserva_extracao(factory, session):
    usuario = _usuario_pago(factory, session)
    with pytest.raises(UnsupportedPlatformError):
        extract_audio_from_url(session, user=usuario, url="https://example.com/video")
    assert session.query(AudioExtraction).filter_by(user_id=usuario.id).count() == 0


# ============================================================================ plano/limite (services.audio_usage real)
def test_usuario_free_e_recusado_antes_de_qualquer_download(factory, session):
    usuario = factory.user()  # sem entitlement pago
    with patch("services.audio_flow.download_audio_source") as mock_download:
        with pytest.raises(AudioNotAllowedError):
            extract_audio_from_url(session, user=usuario, url="https://www.tiktok.com/@a/video/1")
    mock_download.assert_not_called()
    assert session.query(AudioExtraction).count() == 0


def test_limite_diario_esgotado_nao_chama_download(factory, session):
    usuario = _usuario_pago(factory, session, "vip_batch")
    for i in range(15):
        from services.audio_usage import complete_audio_extraction, reserve_audio_extraction

        reserva = reserve_audio_extraction(session, user_id=usuario.id, request_id=f"pre-{i}", now=now_sp())
        complete_audio_extraction(session, reserva.extraction_id, now=now_sp())

    with patch("services.audio_flow.download_audio_source") as mock_download:
        with pytest.raises(AudioQuotaExceededError):
            extract_audio_from_url(session, user=usuario, url="https://www.tiktok.com/@a/video/1")
    mock_download.assert_not_called()
    assert session.query(AudioExtraction).filter_by(user_id=usuario.id).count() == 15  # nenhuma a mais


# ============================================================================ falha após reserva (não conta no limite)
def test_download_falha_marca_failed_e_libera_o_limite(factory, session):
    usuario = _usuario_pago(factory, session)
    antes = get_audio_allowance(session, usuario.id).remaining

    with patch("services.audio_flow.download_audio_source", side_effect=DownloadFailedError("falha")):
        with pytest.raises(DownloadFailedError):
            extract_audio_from_url(session, user=usuario, url="https://www.tiktok.com/@a/video/1")

    session.expire_all()
    (linha,) = session.query(AudioExtraction).filter_by(user_id=usuario.id).all()
    assert linha.status == "failed"
    assert linha.error_code == "DownloadFailedError"
    assert get_audio_allowance(session, usuario.id).remaining == antes


def test_video_sem_audio_marca_failed_com_mensagem_exata_e_libera_o_limite(factory, session, tmp_path):
    usuario = _usuario_pago(factory, session)
    antes = get_audio_allowance(session, usuario.id).remaining
    download_path, _ = _files(tmp_path)

    with patch("services.audio_flow.download_audio_source", return_value=fake_audio_download_result(download_path)), \
         patch("services.audio_flow.process_audio", side_effect=NoAudioStreamError("sem audio")):
        with pytest.raises(NoAudioStreamError) as erro:
            extract_audio_from_url(session, user=usuario, url="https://www.tiktok.com/@a/video/1")

    assert erro.value.user_message == "Esse vídeo não tem áudio."
    session.expire_all()
    (linha,) = session.query(AudioExtraction).filter_by(user_id=usuario.id).all()
    assert linha.status == "failed" and linha.error_code == "NoAudioStreamError"
    assert get_audio_allowance(session, usuario.id).remaining == antes


def test_processamento_falha_marca_failed_e_libera_o_limite(factory, session, tmp_path):
    usuario = _usuario_pago(factory, session)
    antes = get_audio_allowance(session, usuario.id).remaining
    download_path, _ = _files(tmp_path)

    with patch("services.audio_flow.download_audio_source", return_value=fake_audio_download_result(download_path)), \
         patch("services.audio_flow.process_audio", side_effect=FfmpegFailedError("falha")):
        with pytest.raises(FfmpegFailedError):
            extract_audio_from_url(session, user=usuario, url="https://www.tiktok.com/@a/video/1")

    session.expire_all()
    (linha,) = session.query(AudioExtraction).filter_by(user_id=usuario.id).all()
    assert linha.status == "failed" and linha.error_code == "FfmpegFailedError"
    assert get_audio_allowance(session, usuario.id).remaining == antes
    assert not download_path.exists()


def test_excecao_inesperada_apos_reserva_tambem_marca_failed(factory, session, tmp_path):
    usuario = _usuario_pago(factory, session)
    antes = get_audio_allowance(session, usuario.id).remaining
    download_path, _ = _files(tmp_path)

    with patch("services.audio_flow.download_audio_source", return_value=fake_audio_download_result(download_path)), \
         patch("services.audio_flow.process_audio", side_effect=RuntimeError("bug interno")):
        with pytest.raises(RuntimeError):
            extract_audio_from_url(session, user=usuario, url="https://www.tiktok.com/@a/video/1")

    session.expire_all()
    (linha,) = session.query(AudioExtraction).filter_by(user_id=usuario.id).all()
    assert linha.status == "failed" and linha.error_code == "RuntimeError"
    assert get_audio_allowance(session, usuario.id).remaining == antes


# ============================================================================ falha de persistência
def test_falha_ao_gravar_complete_nao_mascara_como_erro_de_audio(factory, session, tmp_path):
    usuario = _usuario_pago(factory, session)
    download_path, output_path = _files(tmp_path)

    with patch("services.audio_flow.download_audio_source", return_value=fake_audio_download_result(download_path)), \
         patch("services.audio_flow.process_audio", return_value=fake_audio_processing_result(output_path)), \
         patch("services.audio_flow.complete_audio_extraction", side_effect=RuntimeError("banco fora do ar")):
        with pytest.raises(AudioPersistenceError) as erro:
            extract_audio_from_url(session, user=usuario, url="https://www.tiktok.com/@a/video/1")

    assert erro.value.phase == "complete"
    assert not download_path.exists() and not output_path.exists()


def test_falha_ao_gravar_fail_preserva_a_causa_original(factory, session):
    usuario = _usuario_pago(factory, session)
    with patch("services.audio_flow.download_audio_source", side_effect=DownloadFailedError("x")), \
         patch("services.audio_flow.fail_audio_extraction", side_effect=RuntimeError("banco fora do ar")):
        with pytest.raises(AudioPersistenceError) as erro:
            extract_audio_from_url(session, user=usuario, url="https://www.tiktok.com/@a/video/1")
    assert erro.value.phase == "fail"
    assert isinstance(erro.value.__cause__, DownloadFailedError)


# ============================================================================ isolamento entre usuários
def test_duas_extracoes_de_usuarios_diferentes_nao_se_misturam(factory, session, tmp_path):
    usuario_a = _usuario_pago(factory, session)
    usuario_b = _usuario_pago(factory, session)
    download_a, output_a = _files(tmp_path, "a-down.m4a", "a-saida.mp3")
    download_b, output_b = _files(tmp_path, "b-down.m4a", "b-saida.mp3")

    with patch("services.audio_flow.download_audio_source", return_value=fake_audio_download_result(download_a)), \
         patch("services.audio_flow.process_audio", return_value=fake_audio_processing_result(output_a, sha="1" * 64)):
        resultado_a = extract_audio_from_url(session, user=usuario_a, url="https://www.tiktok.com/@a/video/1")

    with patch("services.audio_flow.download_audio_source", return_value=fake_audio_download_result(download_b)), \
         patch("services.audio_flow.process_audio", return_value=fake_audio_processing_result(output_b, sha="2" * 64)):
        resultado_b = extract_audio_from_url(session, user=usuario_b, url="https://www.tiktok.com/@a/video/1")

    session.expire_all()
    linha_a = session.get(AudioExtraction, resultado_a.extraction_id)
    linha_b = session.get(AudioExtraction, resultado_b.extraction_id)
    assert linha_a.user_id == usuario_a.id and linha_b.user_id == usuario_b.id
    assert get_audio_allowance(session, usuario_a.id).used == 1
    assert get_audio_allowance(session, usuario_b.id).used == 1


# ============================================================================ storage do resultado
def test_sucesso_persiste_os_campos_de_resultado_com_extensao_mp3(factory, session, tmp_path):
    usuario = _usuario_pago(factory, session)
    download_path, output_path = _files(tmp_path)

    with patch("services.audio_flow.download_audio_source", return_value=fake_audio_download_result(download_path)), \
         patch("services.audio_flow.process_audio", return_value=fake_audio_processing_result(output_path, size=4321)):
        resultado = extract_audio_from_url(session, user=usuario, url="https://www.tiktok.com/@a/video/1")

    session.expire_all()
    linha = session.get(AudioExtraction, resultado.extraction_id)
    assert linha.output_size_bytes == 4321
    assert linha.output_storage_key is not None and len(linha.output_storage_key) == 32
    assert linha.output_expires_at is not None
    assert result_storage.exists(linha.output_storage_key, "mp3")


def test_arquivo_processado_e_movido_para_o_storage_nao_copiado(factory, session, tmp_path):
    usuario = _usuario_pago(factory, session)
    download_path, output_path = _files(tmp_path)
    conteudo_original = output_path.read_bytes()

    with patch("services.audio_flow.download_audio_source", return_value=fake_audio_download_result(download_path)), \
         patch("services.audio_flow.process_audio", return_value=fake_audio_processing_result(output_path)):
        resultado = extract_audio_from_url(session, user=usuario, url="https://www.tiktok.com/@a/video/1")

    assert not output_path.exists()
    session.expire_all()
    linha = session.get(AudioExtraction, resultado.extraction_id)
    caminho_no_storage = Path(result_storage.RESULT_STORAGE_DIR) / f"{linha.output_storage_key}.mp3"
    assert caminho_no_storage.read_bytes() == conteudo_original


def test_save_falhando_chama_fail_audio_extraction_e_nao_persiste_resultado(factory, session, tmp_path):
    usuario = _usuario_pago(factory, session)
    antes = get_audio_allowance(session, usuario.id).remaining
    download_path, output_path = _files(tmp_path)

    with patch("services.audio_flow.download_audio_source", return_value=fake_audio_download_result(download_path)), \
         patch("services.audio_flow.process_audio", return_value=fake_audio_processing_result(output_path)), \
         patch("services.audio_flow.result_storage.save", side_effect=OSError("disco cheio")):
        with pytest.raises(OSError):
            extract_audio_from_url(session, user=usuario, url="https://www.tiktok.com/@a/video/1")

    session.expire_all()
    (linha,) = session.query(AudioExtraction).filter_by(user_id=usuario.id).all()
    assert linha.status == "failed" and linha.error_code == "OSError"
    assert linha.output_storage_key is None and linha.output_size_bytes is None
    assert get_audio_allowance(session, usuario.id).remaining == antes


# ============================================================================ nome de arquivo (filename opcional)
def test_filename_opcional_e_sanitizado_com_sufixo_mp3(factory, session, tmp_path):
    usuario = _usuario_pago(factory, session)
    download_path, output_path = _files(tmp_path)

    with patch("services.audio_flow.download_audio_source", return_value=fake_audio_download_result(download_path)), \
         patch("services.audio_flow.process_audio", return_value=fake_audio_processing_result(output_path)):
        resultado = extract_audio_from_url(
            session, user=usuario, url="https://www.tiktok.com/@a/video/1", filename="minha musica",
        )

    session.expire_all()
    linha = session.get(AudioExtraction, resultado.extraction_id)
    assert linha.display_filename.endswith(".mp3")
    assert not linha.display_filename.endswith(".mp4")
