"""services/generation_flow.run_batch (Etapa 9.3): orquestração de um lote JÁ RESERVADO,
reaproveitando o mesmo núcleo (_execute_reserved) da geração avulsa. Reserva feita diretamente via
services.usage.reserve_batch (não passa pela rota HTTP -- isso é coberto em test_batch_routes.py).
"""
from unittest.mock import patch

import pytest

from download.errors import DownloadFailedError
from download.platform import Platform
from helpers_generation_flow import fake_download_result, fake_processing_result
from helpers_usage import NOW, give
from services.generation_flow import GenerationPersistenceError, run_batch
from services.usage import complete_generation, fail_generation, get_batch_allowance, reserve_batch


def _arquivos(tmp_path, n):
    pares = []
    for i in range(n):
        d = tmp_path / f"baixado{i}.mp4"
        d.write_bytes(b"x")
        o = tmp_path / f"saida{i}.mp4"
        o.write_bytes(b"y")
        pares.append((d, o))
    return pares


def _reservar(factory, session, usuario, size, request_id="lote-1", plan="weekly"):
    give(factory, session, usuario, plan, now=NOW)
    reservation = reserve_batch(session, user_id=usuario.id, request_id=request_id, size=size, now=NOW)
    return reservation


# ============================================================================ sucesso / sequencial
def test_todos_os_itens_completam_com_sucesso(factory, session, tmp_path):
    usuario = factory.user()
    reservation = _reservar(factory, session, usuario, size=3)
    pares_arquivo = _arquivos(tmp_path, 3)
    urls = [f"https://www.tiktok.com/@a/video/{i}" for i in range(3)]
    items = list(zip(reservation.generation_ids, urls))

    download_side_effect = [fake_download_result(d, platform=Platform.TIKTOK) for d, _ in pares_arquivo]
    processing_side_effect = [fake_processing_result(o, sha=chr(97 + i) * 64) for i, (_, o) in enumerate(pares_arquivo)]

    with patch("services.generation_flow.download_video", side_effect=download_side_effect), \
         patch("services.generation_flow.process_video", side_effect=processing_side_effect):
        resultados = run_batch(session, items=items)

    assert len(resultados) == 3
    assert all(r.ok for r in resultados)
    assert [r.position for r in resultados] == [1, 2, 3]
    assert [r.outcome.output_sha256 for r in resultados] == ["a" * 64, "b" * 64, "c" * 64]


def test_processamento_e_sequencial_um_item_por_vez(factory, session, tmp_path):
    """download_video só deve ser chamado a próxima vez depois que o item anterior já terminou --
    comprovado indiretamente pela ORDEM determinística dos side_effects (uma lista, não side
    effects concorrentes) e pelo número exato de chamadas (nem mais, nem menos)."""
    usuario = factory.user()
    reservation = _reservar(factory, session, usuario, size=2)
    pares_arquivo = _arquivos(tmp_path, 2)
    items = list(zip(reservation.generation_ids, ["https://www.tiktok.com/@a/1", "https://www.tiktok.com/@a/2"]))

    with patch("services.generation_flow.download_video", side_effect=[fake_download_result(d) for d, _ in pares_arquivo]) as mock_download, \
         patch("services.generation_flow.process_video", side_effect=[fake_processing_result(o) for _, o in pares_arquivo]) as mock_process:
        run_batch(session, items=items)

    assert mock_download.call_count == 2
    assert mock_process.call_count == 2


# ============================================================================ isolamento de falha
def test_falha_de_um_item_nao_cancela_os_demais(factory, session, tmp_path):
    usuario = factory.user()
    reservation = _reservar(factory, session, usuario, size=3)
    pares_arquivo = _arquivos(tmp_path, 3)
    items = list(zip(reservation.generation_ids, [f"https://www.tiktok.com/@a/{i}" for i in range(3)]))

    download_side_effect = [
        fake_download_result(pares_arquivo[0][0]),
        DownloadFailedError("falhou o item do meio"),
        fake_download_result(pares_arquivo[2][0]),
    ]
    processing_side_effect = [fake_processing_result(pares_arquivo[0][1]), fake_processing_result(pares_arquivo[2][1])]

    with patch("services.generation_flow.download_video", side_effect=download_side_effect), \
         patch("services.generation_flow.process_video", side_effect=processing_side_effect):
        resultados = run_batch(session, items=items)

    assert [r.ok for r in resultados] == [True, False, True]
    assert resultados[1].error_code == "DownloadFailedError"
    assert resultados[1].outcome is None
    assert resultados[0].outcome is not None and resultados[2].outcome is not None


def test_falha_de_item_nao_devolve_a_vaga_de_operacao_de_lote(factory, session, tmp_path):
    """A cota de OPERAÇÕES de lote (Etapa 9.2) é consumida na RESERVA (reserve_batch), antes de
    run_batch existir -- uma falha de item processada aqui nunca a devolve nem a toca de novo."""
    usuario = factory.user()
    reservation = _reservar(factory, session, usuario, size=1, plan="weekly")
    antes = get_batch_allowance(session, usuario.id, now=NOW)
    items = list(zip(reservation.generation_ids, ["https://www.tiktok.com/@a/1"]))

    with patch("services.generation_flow.download_video", side_effect=DownloadFailedError("falhou")):
        run_batch(session, items=items)

    depois = get_batch_allowance(session, usuario.id, now=NOW)
    assert depois.used == antes.used  # nada mudou: a operação já tinha sido gasta na reserva


def test_falha_de_item_libera_a_cota_de_video_apenas_daquele_item(factory, session, tmp_path):
    from services.usage import get_allowance

    usuario = factory.user()
    reservation = _reservar(factory, session, usuario, size=2)
    pares_arquivo = _arquivos(tmp_path, 1)
    items = list(zip(reservation.generation_ids, ["https://www.tiktok.com/@a/1", "https://www.tiktok.com/@a/2"]))

    with patch("services.generation_flow.download_video", side_effect=[DownloadFailedError("falhou"), fake_download_result(pares_arquivo[0][0])]), \
         patch("services.generation_flow.process_video", return_value=fake_processing_result(pares_arquivo[0][1])):
        run_batch(session, items=items)

    # 1 vídeo consumido de verdade (completed) + 1 liberado (failed) = usado real é 1, não 2.
    assert get_allowance(session, usuario.id, now=NOW).used == 1


# ============================================================================ falha sistêmica de persistência
def test_falha_de_persistencia_aborta_o_restante_do_lote(factory, session, tmp_path):
    usuario = factory.user()
    reservation = _reservar(factory, session, usuario, size=3)
    pares_arquivo = _arquivos(tmp_path, 3)
    items = list(zip(reservation.generation_ids, [f"https://www.tiktok.com/@a/{i}" for i in range(3)]))

    with patch("services.generation_flow.download_video", side_effect=[fake_download_result(d) for d, _ in pares_arquivo]), \
         patch("services.generation_flow.process_video", side_effect=[fake_processing_result(o) for _, o in pares_arquivo]), \
         patch("services.generation_flow.complete_generation", side_effect=RuntimeError("banco caiu")), \
         patch("services.generation_flow.fail_generation", side_effect=RuntimeError("banco caiu de novo")):
        with pytest.raises(GenerationPersistenceError):
            run_batch(session, items=items)


# ============================================================================ reexecução idempotente (replay de lote)
def test_item_ja_completed_nao_e_reprocessado(factory, session, tmp_path):
    usuario = factory.user()
    reservation = _reservar(factory, session, usuario, size=2)
    pares_arquivo = _arquivos(tmp_path, 2)
    gen_id_1, gen_id_2 = reservation.generation_ids

    # simula que o item 1 já foi processado numa tentativa anterior (ex.: processo caiu depois)
    complete_generation(
        session, gen_id_1, output_sha256="f" * 64, duration_ms=1000, output_size_bytes=10,
        output_expires_at=None, output_storage_key="x" * 32, now=NOW,
    )

    items = [(gen_id_1, "https://www.tiktok.com/@a/1"), (gen_id_2, "https://www.tiktok.com/@a/2")]
    with patch("services.generation_flow.download_video", side_effect=[fake_download_result(pares_arquivo[1][0])]) as mock_download, \
         patch("services.generation_flow.process_video", side_effect=[fake_processing_result(pares_arquivo[1][1])]):
        resultados = run_batch(session, items=items)

    assert mock_download.call_count == 1  # o item 1 (já completed) nunca chamou download_video
    assert resultados[0].ok is True and resultados[0].outcome.output_sha256 == "f" * 64
    assert resultados[1].ok is True


def test_item_ja_failed_e_relatado_sem_reprocessar(factory, session, tmp_path):
    usuario = factory.user()
    reservation = _reservar(factory, session, usuario, size=1)
    gen_id = reservation.generation_ids[0]
    fail_generation(session, gen_id, error_code="DownloadFailedError", now=NOW)

    with patch("services.generation_flow.download_video") as mock_download:
        resultados = run_batch(session, items=[(gen_id, "https://www.tiktok.com/@a/1")])

    mock_download.assert_not_called()
    assert resultados[0].ok is False
    assert resultados[0].error_code == "DownloadFailedError"


# ============================================================================ callback de progresso
def test_on_item_progress_recebe_posicao_e_generation_id_corretos(factory, session, tmp_path):
    usuario = factory.user()
    reservation = _reservar(factory, session, usuario, size=2)
    pares_arquivo = _arquivos(tmp_path, 2)
    items = list(zip(reservation.generation_ids, ["https://www.tiktok.com/@a/1", "https://www.tiktok.com/@a/2"]))
    chamadas = []

    def on_progress(position, generation_id, stage, percent):
        chamadas.append((position, generation_id, stage))

    with patch("services.generation_flow.download_video", side_effect=[fake_download_result(d) for d, _ in pares_arquivo]), \
         patch("services.generation_flow.process_video", side_effect=[fake_processing_result(o) for _, o in pares_arquivo]):
        run_batch(session, items=items, on_item_progress=on_progress)

    posicoes_vistas = {c[0] for c in chamadas}
    assert posicoes_vistas == {1, 2}
    assert all(c[1] in reservation.generation_ids for c in chamadas)
    assert (1, reservation.generation_ids[0], "download") in chamadas
    assert (2, reservation.generation_ids[1], "download") in chamadas