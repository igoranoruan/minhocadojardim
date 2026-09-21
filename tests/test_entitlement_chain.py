"""Regras puras da cadeia de entitlements (sem banco): sobreposição, empilhamento e realinhamento."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from services.entitlement_chain import (
    find_overlaps,
    is_current,
    next_window,
    overlaps_involving_current,
    realign_future,
)

UTC = timezone.utc
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
DIA = timedelta(days=1)


@dataclass
class Item:
    id: int
    starts_at: datetime
    expires_at: datetime
    duration_days: int


def item(id_, inicio_dias, dias):
    inicio = NOW + inicio_dias * DIA
    return Item(id_, inicio, inicio + dias * DIA, dias)


def test_intervalo_e_fechado_no_inicio_e_aberto_no_fim():
    a = item(1, 0, 7)
    assert is_current(a, a.starts_at) and not is_current(a, a.expires_at)
    assert not is_current(a, a.starts_at - timedelta(seconds=1))


def test_acessos_colados_nao_se_sobrepoem():
    assert find_overlaps([item(1, 0, 7), item(2, 7, 30)]) == []


def test_sobreposicao_e_detectada_mesmo_com_itens_fora_de_ordem():
    a, b, c = item(1, 0, 7), item(2, 5, 7), item(3, 30, 7)
    assert find_overlaps([c, b, a]) == [(a, b)]


def test_sobreposicao_com_item_aninhado_e_detectada():
    grande, pequeno = item(1, 0, 30), item(2, 3, 2)
    assert find_overlaps([pequeno, grande]) == [(grande, pequeno)]


def test_sobreposicao_que_envolve_o_vigente_e_separada_da_futura():
    vigente, futuro_ok, f1, f2 = item(1, -1, 7), item(2, 6, 7), item(3, 20, 7), item(4, 22, 7)
    itens = [vigente, futuro_ok, f1, f2]
    assert overlaps_involving_current(itens, NOW) == []  # o vigente termina em +6 e o próximo começa em +6
    com_erro = [item(1, -1, 9), item(2, 6, 7)]
    assert overlaps_involving_current(com_erro, NOW) == [(com_erro[0], com_erro[1])]


def test_empilhamento_sem_acesso_comeca_agora():
    assert next_window([], NOW, 7) == (NOW, NOW + 7 * DIA)


def test_empilhamento_comeca_quando_termina_o_ultimo_e_nao_perde_dias():
    a = item(1, -1, 8)  # 01/10-1 -> +7
    inicio, fim = next_window([a], NOW, 30)
    assert inicio == a.expires_at and fim - inicio == 30 * DIA
    b = Item(2, inicio, fim, 30)
    inicio2, fim2 = next_window([a, b], NOW, 7)
    assert inicio2 == b.expires_at and fim2 - inicio2 == 7 * DIA
    assert find_overlaps([a, b, Item(3, inicio2, fim2, 7)]) == []


def test_realinhar_nao_mexe_no_vigente_e_fecha_o_buraco_dos_futuros():
    vigente = item(1, -2, 7)  # termina em +5
    d = item(4, 30, 7)  # ficou longe porque houve revogações no meio
    b = item(2, 5, 7)
    mudancas = realign_future([vigente, d, b], NOW)
    assert [(i.id, ini, fim) for i, ini, fim in mudancas] == [(4, b.expires_at, b.expires_at + 7 * DIA)]
    assert vigente.starts_at == NOW - 2 * DIA  # o vigente nunca é movido


def test_exemplo_aprovado_a_revogado_b_valido_c_revogado_d_valido():
    # A e C revogados já não estão na lista. B e D válidos, D longe de B.
    b, d = item(2, 5, 7), item(4, 42, 7)
    mudancas = realign_future([b, d], NOW)
    resultado = {i.id: (ini, fim) for i, ini, fim in mudancas}
    assert resultado[2] == (NOW, NOW + 7 * DIA)  # B começa agora (sem vigente)
    assert resultado[4] == (NOW + 7 * DIA, NOW + 14 * DIA)  # D logo depois de B
    assert find_overlaps([Item(2, *resultado[2], 7), Item(4, *resultado[4], 7)]) == []


def test_realinhar_mantem_a_duracao_e_a_ordem():
    b, c = item(2, 3, 7), item(3, 20, 30)
    mudancas = realign_future([c, b], NOW)
    novos = {i.id: (ini, fim) for i, ini, fim in mudancas}
    assert novos[2][1] - novos[2][0] == 7 * DIA and novos[3][1] - novos[3][0] == 30 * DIA
    assert novos[2][1] == novos[3][0]


def test_realinhar_sem_mudanca_devolve_lista_vazia():
    vigente, futuro = item(1, -1, 8), item(2, 7, 30)
    assert realign_future([vigente, futuro], NOW) == []
