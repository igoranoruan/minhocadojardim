"""services/audio_flow.run_audio_batch (Parte B, autorização do CÉREBRO, 11/10/2026): orquestração
de um LOTE de áudio já reservado, reaproveitando o mesmo núcleo (execute_reserved_audio_extraction)
da extração avulsa. Reserva feita diretamente via services.audio_usage.reserve_audio_batch (não
passa pela rota HTTP -- isso é coberto em test_audio_batch_routes.py).

Mesmo padrão de tests/test_batch_flow_service.py (lote de vídeo)."""
from unittest.mock import patch

import pytest

from download.errors import DownloadFailedError
from download.platform import Platform
from helpers_audio import fake_audio_download_result, fake_audio_processing_result
from helpers_usage import NOW, give
from services.audio_flow import AudioPersistenceError, run_audio_batch
from services.audio_usage import complete_audio_extraction, fail_audio_extraction, reserve_audio_batch


@pytest.fixture(autouse=True)
def diretorio_de_storage(tmp_path, monkeypatch):
    monkeypatch.setattr("services.result_storage.RESULT_STORAGE_DIR", str(tmp_path / "results"))


def _arquivos(tmp_path, n):
    pares = []
    for i in range(n):
        d = tmp_path / f"fonte{i}.m4a"
        d.write_bytes(b"x")
        o = tmp_path / f"saida{i}.mp3"
        o.write_bytes(b"y")
        pares.append((d, o))
    return pares


def _reservar(factory, session, usuario, size, request_id="lote-audio-1", plan="vip_batch"):
    give(factory, session, usuario, plan, now=NOW)
    return reserve_audio_batch(session, user_id=usuario.id, request_id=request_id, size=size, now=NOW)


# ============================================================================ sucesso / sequencial
def test_todos_os_itens_completam_com_sucesso(factory, session, tmp_path):
    usuario = factory.user()
    reservation = _reservar(factory, session, usuario, size=3)
    pares_arquivo = _arquivos(tmp_path, 3)
    urls = [f"https://www.tiktok.com/@a/video/{i}" for i in range(3)]
    items = list(zip(reservation.extraction_ids, urls))

    download_side_effect = [fake_audio_download_result(d, platform=Platform.TIKTOK) for d, _ in pares_arquivo]
    processing_side_effect = [fake_audio_processing_result(o, sha=chr(97 + i) * 64) for i, (_, o) in enumerate(pares_arquivo)]

    with patch("services.audio_flow.download_audio_source", side_effect=download_side_effect), \
         patch("services.audio_flow.process_audio", side_effect=processing_side_effect):
        resultados = run_audio_batch(session, items=items)

    assert len(resultados) == 3
    assert all(r.ok for r in resultados)
    assert [r.position for r in resultados] == [1, 2, 3]
    assert [r.outcome.output_sha256 for r in resultados] == ["a" * 64, "b" * 64, "c" * 64]


def test_processamento_e_sequencial_um_item_por_vez(factory, session, tmp_path):
    usuario = factory.user()
    reservation = _reservar(factory, session, usuario, size=2)
    pares_arquivo = _arquivos(tmp_path, 2)
    items = list(zip(reservation.extraction_ids, ["https://www.tiktok.com/@a/1", "https://www.tiktok.com/@a/2"]))

    with patch("services.audio_flow.download_audio_source", side_effect=[fake_audio_download_result(d) for d, _ in pares_arquivo]) as mock_download, \
         patch("services.audio_flow.process_audio", side_effect=[fake_audio_processing_result(o) for _, o in pares_arquivo]) as mock_process:
        run_audio_batch(session, items=items)

    assert mock_download.call_count == 2
    assert mock_process.call_count == 2


# ============================================================================ isolamento de falha
def test_falha_de_um_item_nao_cancela_os_demais(factory, session, tmp_path):
    usuario = factory.user()
    reservation = _reservar(factory, session, usuario, size=3)
    pares_arquivo = _arquivos(tmp_path, 3)
    items = list(zip(reservation.extraction_ids, [f"https://www.tiktok.com/@a/{i}" for i in range(3)]))

    download_side_effect = [
        fake_audio_download_result(pares_arquivo[0][0]),
        DownloadFailedError("falhou o item do meio"),
        fake_audio_download_result(pares_arquivo[2][0]),
    ]
    processing_side_effect = [fake_audio_processing_result(pares_arquivo[0][1]), fake_audio_processing_result(pares_arquivo[2][1])]

    with patch("services.audio_flow.download_audio_source", side_effect=download_side_effect), \
         patch("services.audio_flow.process_audio", side_effect=processing_side_effect):
        resultados = run_audio_batch(session, items=items)

    assert [r.ok for r in resultados] == [True, False, True]
    assert resultados[1].error_code == "DownloadFailedError"
    assert resultados[1].outcome is None
    assert resultados[0].outcome is not None and resultados[2].outcome is not None


def test_falha_de_item_nao_devolve_a_vaga_do_lote_mas_libera_o_limite_diario_so_daquele_item(factory, session, tmp_path):
    from services.audio_usage import get_audio_allowance

    usuario = factory.user()
    reservation = _reservar(factory, session, usuario, size=2, plan="weekly")
    pares_arquivo = _arquivos(tmp_path, 1)
    items = list(zip(reservation.extraction_ids, ["https://www.tiktok.com/@a/1", "https://www.tiktok.com/@a/2"]))

    with patch("services.audio_flow.download_audio_source", side_effect=[DownloadFailedError("falhou"), fake_audio_download_result(pares_arquivo[0][0])]), \
         patch("services.audio_flow.process_audio", return_value=fake_audio_processing_result(pares_arquivo[0][1])):
        run_audio_batch(session, items=items)

    # 1 extração consumida de verdade (completed) + 1 liberada (failed) = usado real é 1, não 2.
    assert get_audio_allowance(session, usuario.id, now=NOW).used == 1


# ============================================================================ falha sistêmica de persistência
def test_falha_de_persistencia_aborta_o_restante_do_lote(factory, session, tmp_path):
    usuario = factory.user()
    reservation = _reservar(factory, session, usuario, size=3)
    pares_arquivo = _arquivos(tmp_path, 3)
    items = list(zip(reservation.extraction_ids, [f"https://www.tiktok.com/@a/{i}" for i in range(3)]))

    with patch("services.audio_flow.download_audio_source", side_effect=[fake_audio_download_result(d) for d, _ in pares_arquivo]), \
         patch("services.audio_flow.process_audio", side_effect=[fake_audio_processing_result(o) for _, o in pares_arquivo]), \
         patch("services.audio_flow.complete_audio_extraction", side_effect=RuntimeError("banco caiu")), \
         patch("services.audio_flow.fail_audio_extraction", side_effect=RuntimeError("banco caiu de novo")):
        with pytest.raises(AudioPersistenceError):
            run_audio_batch(session, items=items)


# ============================================================================ reexecução idempotente (replay de lote)
def test_item_ja_completed_nao_e_reprocessado(factory, session, tmp_path):
    usuario = factory.user()
    reservation = _reservar(factory, session, usuario, size=2)
    pares_arquivo = _arquivos(tmp_path, 2)
    eid_1, eid_2 = reservation.extraction_ids

    complete_audio_extraction(
        session, eid_1, output_sha256="f" * 64, duration_ms=1000, output_size_bytes=10,
        output_expires_at=None, output_storage_key=None, now=NOW,
    )

    items = [(eid_1, "https://www.tiktok.com/@a/1"), (eid_2, "https://www.tiktok.com/@a/2")]
    with patch("services.audio_flow.download_audio_source", side_effect=[fake_audio_download_result(pares_arquivo[1][0])]) as mock_download, \
         patch("services.audio_flow.process_audio", side_effect=[fake_audio_processing_result(pares_arquivo[1][1])]):
        resultados = run_audio_batch(session, items=items)

    assert mock_download.call_count == 1  # o item 1 (já completed) nunca chamou download_audio_source
    assert resultados[0].ok is True and resultados[0].outcome.output_sha256 == "f" * 64
    assert resultados[1].ok is True


def test_item_ja_failed_e_relatado_sem_reprocessar(factory, session, tmp_path):
    usuario = factory.user()
    reservation = _reservar(factory, session, usuario, size=1)
    eid = reservation.extraction_ids[0]
    fail_audio_extraction(session, eid, error_code="DownloadFailedError", now=NOW)

    with patch("services.audio_flow.download_audio_source") as mock_download:
        resultados = run_audio_batch(session, items=[(eid, "https://www.tiktok.com/@a/1")])

    mock_download.assert_not_called()
    assert resultados[0].ok is False
    assert resultados[0].error_code == "DownloadFailedError"


# ============================================================================ callback de progresso
def test_on_item_progress_recebe_posicao_e_extraction_id_corretos(factory, session, tmp_path):
    usuario = factory.user()
    reservation = _reservar(factory, session, usuario, size=2)
    pares_arquivo = _arquivos(tmp_path, 2)
    items = list(zip(reservation.extraction_ids, ["https://www.tiktok.com/@a/1", "https://www.tiktok.com/@a/2"]))
    chamadas = []

    def on_progress(position, extraction_id, stage, percent, platform):
        chamadas.append((position, extraction_id, stage))

    with patch("services.audio_flow.download_audio_source", side_effect=[fake_audio_download_result(d) for d, _ in pares_arquivo]), \
         patch("services.audio_flow.process_audio", side_effect=[fake_audio_processing_result(o) for _, o in pares_arquivo]):
        run_audio_batch(session, items=items, on_item_progress=on_progress)

    posicoes_vistas = {c[0] for c in chamadas}
    assert posicoes_vistas == {1, 2}
    assert all(c[1] in reservation.extraction_ids for c in chamadas)
    assert (1, reservation.extraction_ids[0], "download") in chamadas
    assert (2, reservation.extraction_ids[1], "download") in chamadas


# ============================================================================ nunca consome generation/batch de vídeo
def test_lote_de_audio_processado_nunca_cria_generation(factory, session, tmp_path):
    from sqlalchemy import func, select

    from database.models import Generation

    usuario = factory.user()
    reservation = _reservar(factory, session, usuario, size=2)
    pares_arquivo = _arquivos(tmp_path, 2)
    items = list(zip(reservation.extraction_ids, ["https://www.tiktok.com/@a/1", "https://www.tiktok.com/@a/2"]))

    with patch("services.audio_flow.download_audio_source", side_effect=[fake_audio_download_result(d) for d, _ in pares_arquivo]), \
         patch("services.audio_flow.process_audio", side_effect=[fake_audio_processing_result(o) for _, o in pares_arquivo]):
        run_audio_batch(session, items=items)

    assert session.execute(select(func.count()).select_from(Generation)).scalar_one() == 0
