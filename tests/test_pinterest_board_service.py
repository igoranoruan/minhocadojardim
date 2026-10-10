"""Orquestração da listagem de pasta do Pinterest (Etapa 3, 10/10/2026 -- aprovação do CÉREBRO):
gate por plano (lote habilitado) + rate limit por usuário. Usa banco real (fixtures de
tests/conftest.py) para o gate de plano -- NÃO chama yt-dlp de verdade (list_board_pins é sempre
mockado aqui; a extração em si é tests/test_pinterest_board_listing.py).
"""
from unittest.mock import patch

import pytest

import services.pinterest_board as pinterest_board
from download.board_listing import BoardListing, BoardPin
from download.errors import DownloadFailedError
from services.pinterest_board import (
    BoardListingPlanNotAllowedError,
    BoardListingRateLimitedError,
    PinterestBoardError,
    list_board,
)


@pytest.fixture(autouse=True)
def _limpar_rate_limit():
    """O contador é em memória, em dict de módulo (ver services/pinterest_board.py) -- sem isso,
    o estado de um teste vazaria para o próximo (mesmo processo pytest)."""
    pinterest_board._attempts_by_user.clear()
    yield
    pinterest_board._attempts_by_user.clear()


def _listing(n=1):
    return BoardListing(
        pins=[BoardPin(pin_id=str(i), pin_url=f"https://x/{i}", thumbnail_url=None, has_video=True) for i in range(n)],
        has_more=False,
    )


# ------------------------------------------------------------------ gate por plano
def test_free_nao_pode_listar(session, factory):
    user = factory.user()  # sem entitlement = Free
    with pytest.raises(BoardListingPlanNotAllowedError) as exc_info:
        list_board(session, user, "https://www.pinterest.com/alguem/pasta/")
    assert exc_info.value.status_code == 403


def test_plano_com_lote_pode_listar(session, factory):
    from database.models import User

    entitlement = factory.entitlement(plan_code="weekly")  # Semanal: batch_enabled=True
    user = session.get(User, entitlement.user_id)
    with patch("services.pinterest_board.list_board_pins", return_value=_listing(2)) as mock_list:
        listing = list_board(session, user, "https://www.pinterest.com/alguem/pasta/")
    assert len(listing.pins) == 2
    mock_list.assert_called_once_with("https://www.pinterest.com/alguem/pasta/")


# ------------------------------------------------------------------ rate limit
def test_rate_limit_bloqueia_apos_o_teto(session, factory):
    from config import BOARD_LIST_RATE_LIMIT_PER_HOUR
    from database.models import User

    entitlement = factory.entitlement(plan_code="weekly")
    user = session.get(User, entitlement.user_id)

    with patch("services.pinterest_board.list_board_pins", return_value=_listing()):
        for _ in range(BOARD_LIST_RATE_LIMIT_PER_HOUR):
            list_board(session, user, "https://www.pinterest.com/alguem/pasta/")

        with pytest.raises(BoardListingRateLimitedError) as exc_info:
            list_board(session, user, "https://www.pinterest.com/alguem/pasta/")
    assert exc_info.value.status_code == 429
    assert exc_info.value.retry_after_seconds > 0


def test_rate_limit_e_por_usuario_nunca_compartilhado(session, factory):
    """Dois usuários diferentes não disputam a mesma cota -- o teto de um nunca afeta o outro."""
    from config import BOARD_LIST_RATE_LIMIT_PER_HOUR
    from database.models import User

    ent_a = factory.entitlement(plan_code="weekly")
    ent_b = factory.entitlement(plan_code="weekly")
    user_a = session.get(User, ent_a.user_id)
    user_b = session.get(User, ent_b.user_id)

    with patch("services.pinterest_board.list_board_pins", return_value=_listing()):
        for _ in range(BOARD_LIST_RATE_LIMIT_PER_HOUR):
            list_board(session, user_a, "https://www.pinterest.com/alguem/pasta/")
        # usuário B ainda tem seu próprio teto inteiro disponível.
        list_board(session, user_b, "https://www.pinterest.com/alguem/pasta/")


# ------------------------------------------------------------------ erro de extração
def test_falha_na_extracao_vira_pinterest_board_error_generico(session, factory):
    from database.models import User

    entitlement = factory.entitlement(plan_code="weekly")
    user = session.get(User, entitlement.user_id)

    erro = DownloadFailedError("detalhe técnico que nunca deve vazar")
    with patch("services.pinterest_board.list_board_pins", side_effect=erro):
        with pytest.raises(PinterestBoardError) as exc_info:
            list_board(session, user, "https://www.pinterest.com/alguem/pasta/")
    # a mensagem exposta é a GENÉRICA do erro (user_message), nunca o detalhe técnico.
    assert exc_info.value.detail == erro.user_message
