"""Fila de processamento (pedido e aprovação do Igor, 02/11/2026): no máximo N downloads+
processamentos ao mesmo tempo; quem passa disso espera com "Na fila…"; passou do teto de espera,
o item falha com "servidor ocupado" e a cota volta.

Os testes trocam o semáforo global por um de 1 vaga (e ocupam essa vaga quando querem simular a
fila cheia) e encurtam a espera via monkeypatch -- nunca dependem de tempo real longo."""
import json
import threading
import time
from unittest.mock import patch

import pytest

import services.processing_queue as fila
from config import GENERATION_RESERVATION_TTL_SECONDS, MAX_CONCURRENT_PROCESSING
from database.models import Generation
from download.errors import ServerBusyError
from helpers_generation_flow import fake_download_result, fake_processing_result, login_directly
from helpers_usage import NOW, give
from services.generation_flow import generate_from_url, run_batch
from services.usage import get_allowance, reserve_batch
from utils.time_sp import resolve_now


@pytest.fixture(autouse=True)
def fila_de_teste(monkeypatch, tmp_path):
    monkeypatch.setattr(fila, "_slots", threading.BoundedSemaphore(1))
    monkeypatch.setattr(fila, "PROCESSING_QUEUE_MAX_WAIT_SECONDS", 0.3)
    monkeypatch.setattr(fila, "PROCESSING_QUEUE_POLL_SECONDS", 0.05)
    monkeypatch.setattr("services.result_storage.RESULT_STORAGE_DIR", str(tmp_path / "results"))


@pytest.fixture()
def fila_cheia():
    assert fila._slots.acquire(blocking=False)
    yield
    fila._slots.release()


# ============================================================================ configuração
def test_valores_combinados_com_o_igor():
    assert MAX_CONCURRENT_PROCESSING == 3
    # A espera máxima + o trabalho nunca podem chegar perto do vencimento da reserva.
    from config import PROCESSING_QUEUE_MAX_WAIT_SECONDS
    assert PROCESSING_QUEUE_MAX_WAIT_SECONDS + 6 * 60 < GENERATION_RESERVATION_TTL_SECONDS


# ============================================================================ processing_slot
def test_com_vaga_livre_entra_na_hora_sem_avisar_fila():
    avisos = []
    with fila.processing_slot(on_wait=lambda: avisos.append(1)):
        pass
    assert avisos == []
    assert fila._slots.acquire(blocking=False)  # a vaga foi devolvida
    fila._slots.release()


def test_sem_vaga_avisa_e_entra_quando_alguem_sai():
    avisos = []
    assert fila._slots.acquire(blocking=False)  # alguém ocupando
    threading.Timer(0.1, fila._slots.release).start()
    with fila.processing_slot(on_wait=lambda: avisos.append(1), max_wait_seconds=2, poll_seconds=0.02):
        entrou = True
    assert entrou and len(avisos) >= 1


def test_sem_vaga_ate_o_teto_desiste(fila_cheia):
    inicio = time.monotonic()
    with pytest.raises(fila.QueueWaitTimeoutError):
        with fila.processing_slot():
            pytest.fail("não deveria entrar")
    assert time.monotonic() - inicio < 2


def test_vaga_e_devolvida_mesmo_com_excecao():
    with pytest.raises(RuntimeError):
        with fila.processing_slot():
            raise RuntimeError("boom")
    assert fila._slots.acquire(blocking=False)
    fila._slots.release()


def test_falha_no_aviso_nao_interrompe_a_espera():
    assert fila._slots.acquire(blocking=False)
    threading.Timer(0.1, fila._slots.release).start()

    def _aviso_quebrado():
        raise ConnectionError("cliente saiu")

    with fila.processing_slot(on_wait=_aviso_quebrado, max_wait_seconds=2, poll_seconds=0.02):
        pass


def test_nunca_passa_do_limite_de_simultaneos(monkeypatch):
    monkeypatch.setattr(fila, "_slots", threading.BoundedSemaphore(3))
    ativos = 0
    pico = 0
    trava = threading.Lock()

    def _trabalho():
        nonlocal ativos, pico
        with fila.processing_slot(max_wait_seconds=5, poll_seconds=0.01):
            with trava:
                ativos += 1
                pico = max(pico, ativos)
            time.sleep(0.05)
            with trava:
                ativos -= 1

    threads = [threading.Thread(target=_trabalho) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)
    assert pico == 3


# ============================================================================ geração avulsa
def _arquivos(diretorio, n):
    pares = []
    for i in range(n):
        d = diretorio / f"baixado{i}.mp4"
        d.write_bytes(b"x")
        o = diretorio / f"saida{i}.mp4"
        o.write_bytes(b"y")
        pares.append((d, o))
    return pares


def test_avulso_com_fila_cheia_falha_com_servidor_ocupado_e_devolve_a_cota(factory, session, fila_cheia):
    usuario = factory.user()
    avisos = []
    with patch("services.generation_flow.download_video") as baixar:
        with pytest.raises(ServerBusyError):
            generate_from_url(
                session, user=usuario, url="https://www.tiktok.com/@a/video/1",
                on_progress=lambda stage, percent, platform: avisos.append(stage),
            )
    baixar.assert_not_called()
    assert "queue" in avisos
    session.expire_all()
    linha = session.query(Generation).filter_by(user_id=usuario.id).one()
    assert linha.status == "failed" and linha.error_code == "ServerBusyError"
    assert get_allowance(session, usuario.id).used == 0  # cota devolvida


def test_avulso_com_vaga_funciona_como_sempre(factory, session, tmp_path):
    usuario = factory.user()
    (d, o), = _arquivos(tmp_path, 1)
    with patch("services.generation_flow.download_video", return_value=fake_download_result(d)), \
         patch("services.generation_flow.process_video", return_value=fake_processing_result(o)):
        resultado = generate_from_url(session, user=usuario, url="https://www.tiktok.com/@a/video/1")
    assert resultado.status == "completed"


# ============================================================================ lote
def test_lote_com_fila_cheia_falha_os_itens_pendentes_com_mensagem(factory, session, fila_cheia):
    usuario = factory.user()
    give(factory, session, usuario, "weekly", now=NOW)
    reserva = reserve_batch(session, user_id=usuario.id, request_id="fila-1", size=2, now=NOW)
    items = list(zip(reserva.generation_ids, ["https://www.tiktok.com/@a/video/1", "https://www.tiktok.com/@a/video/2"]))
    avisos = []

    with patch("services.generation_flow.download_video") as baixar:
        resultados = run_batch(
            session, items=items,
            on_item_progress=lambda pos, gid, stage, pct, plat: avisos.append((pos, stage)),
        )

    baixar.assert_not_called()
    assert (1, "queue") in avisos
    assert [r.ok for r in resultados] == [False, False]
    assert all(r.error_code == "ServerBusyError" for r in resultados)
    assert all("muitos pedidos" in r.error_message for r in resultados)
    session.expire_all()
    assert {session.get(Generation, gid).status for gid in reserva.generation_ids} == {"failed"}


def test_lote_ocupa_uma_vaga_so_do_comeco_ao_fim(factory, session, tmp_path, monkeypatch):
    """Com 1 vaga, um lote de 3 itens roda inteiro sem esperar a cada item."""
    usuario = factory.user()
    give(factory, session, usuario, "weekly", now=NOW)
    reserva = reserve_batch(session, user_id=usuario.id, request_id="fila-2", size=3, now=NOW)
    pares = _arquivos(tmp_path, 3)
    items = list(zip(reserva.generation_ids, [f"https://www.tiktok.com/@a/video/{i}" for i in range(3)]))
    avisos = []

    with patch("services.generation_flow.download_video", side_effect=[fake_download_result(d) for d, _ in pares]), \
         patch("services.generation_flow.process_video", side_effect=[fake_processing_result(o) for _, o in pares]):
        resultados = run_batch(
            session, items=items,
            on_item_progress=lambda pos, gid, stage, pct, plat: avisos.append(stage),
        )

    assert all(r.ok for r in resultados)
    assert "queue" not in avisos


# ============================================================================ rota (SSE)
def _eventos(resposta):
    eventos = []
    for bloco in resposta.text.strip("\n").split("\n\n"):
        if bloco.strip():
            linhas = bloco.split("\n")
            eventos.append((linhas[0].removeprefix("event: "), json.loads(linhas[1].removeprefix("data: "))))
    return eventos


def test_rota_avulsa_mostra_na_fila_e_depois_servidor_ocupado(auth_client, factory, session, fila_cheia):
    usuario = factory.user()
    login_directly(auth_client, session, usuario)
    give(factory, session, usuario, "weekly", now=resolve_now())
    with patch("services.generation_flow.download_video"):
        resposta = auth_client.post("/api/generations", json={"url": "https://www.tiktok.com/@a/video/1"})
    eventos = _eventos(resposta)
    assert any(t == "status" and d["message"].startswith("Na fila") for t, d in eventos)
    assert eventos[-1][0] == "error"
    assert "muitos pedidos" in eventos[-1][1]["detail"]
