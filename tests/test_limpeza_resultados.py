"""main.py: varredura dos resultados vencidos na subida e a cada RESULT_SWEEP_INTERVAL_SECONDS
(pedido e aprovação do Igor, 02/11/2026)."""
import threading
from unittest.mock import patch

import main
from config import RESULT_SWEEP_GRACE_SECONDS, RESULT_SWEEP_INTERVAL_SECONDS, RESULT_TTL_SECONDS


def test_intervalo_e_folga_combinados():
    assert RESULT_SWEEP_INTERVAL_SECONDS == 5 * 60
    assert RESULT_SWEEP_GRACE_SECONDS > 0  # nada ainda baixável é apagado


def test_varredura_usa_ttl_mais_folga():
    with patch("main.result_storage.sweep_expired", return_value=(0, 0)) as sweep:
        main._varrer_resultados_vencidos()
    sweep.assert_called_once_with(RESULT_TTL_SECONDS + RESULT_SWEEP_GRACE_SECONDS)


def test_varredura_roda_na_subida_e_depois_periodicamente_ate_parar():
    parar = threading.Event()
    chamadas = []

    def _fake(_idade):
        chamadas.append(1)
        if len(chamadas) >= 3:
            parar.set()
        return (0, 0)

    with patch("main.result_storage.sweep_expired", side_effect=_fake):
        thread = threading.Thread(target=main._loop_limpeza_resultados, args=(parar, 0.01), daemon=True)
        thread.start()
        thread.join(timeout=5)
    assert not thread.is_alive()
    assert len(chamadas) >= 3  # 1 na subida + periódicas


def test_falha_na_varredura_nunca_derruba_o_loop():
    with patch("main.result_storage.sweep_expired", side_effect=OSError("disco")):
        main._varrer_resultados_vencidos()  # não levanta
