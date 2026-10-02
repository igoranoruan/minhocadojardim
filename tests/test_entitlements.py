"""Entitlements: plano vigente, empilhamento sem sobreposição, inconsistência e revogação."""
import logging

import pytest
from sqlalchemy import select

import services.entitlements as ent_service
from database.models import Entitlement
from helpers_usage import DIA, NOW, give, raw_entitlement
from services.entitlement_chain import find_overlaps
from services.entitlements import (
    EntitlementInconsistencyError,
    EntitlementNotFoundError,
    InvalidEntitlementRequestError,
    compute_next_window,
    get_current_entitlement,
    grant_entitlement,
    revoke_entitlement,
)
from services.locks import UserNotFoundError
from services.plans import UnknownPlanError


def _todos(session, user):
    session.expire_all()
    return list(session.execute(select(Entitlement).where(Entitlement.user_id == user.id).order_by(Entitlement.id)).scalars())


# ============================================================================ concessão e empilhamento
@pytest.mark.parametrize(
    "plano, dias", [("weekly", 7), ("monthly", 30), ("vip_batch", 30)], ids=["semanal", "mensal", "vip"]
)
def test_sem_acesso_ativo_o_novo_comeca_imediatamente(factory, session, plano, dias):
    usuario = factory.user()
    acesso = give(factory, session, usuario, plano)
    session.refresh(acesso)
    assert acesso.starts_at == NOW and acesso.expires_at == NOW + dias * DIA
    assert acesso.duration_days == dias and acesso.status == "granted" and acesso.plan_code == plano


def test_comprar_com_um_plano_ja_vigente_troca_na_hora_sem_empilhar(factory, session):
    """TROCA IMEDIATA (decisão do CÉREBRO, 02/10/2026, substitui o empilhamento antigo para este
    caso -- ver o comentário de grant_entitlement): comprar um plano novo enquanto outro já está
    vigente encerra o vigente NA HORA (dias restantes descartados) e o novo começa já. Antes desta
    decisão, este mesmo cenário empilhava o novo depois do vigente terminar (era este teste que
    provava isso, com o nome test_stacking_novo_acesso_comeca_quando_termina_o_atual_sem_sobrepor)
    -- um upgrade real em produção (02/10/2026) mostrou que isso deixava o cliente pagando por um
    plano melhor sem recebê-lo de imediato."""
    usuario = factory.user()
    a = give(factory, session, usuario, "weekly", now=NOW)
    b = give(factory, session, usuario, "monthly", now=NOW + 1 * DIA)  # upgrade DURANTE o acesso A
    c = give(factory, session, usuario, "vip_batch", now=NOW + 3 * DIA)  # upgrade DURANTE o acesso B
    for acesso in (a, b, c):
        session.refresh(acesso)

    assert (a.starts_at, a.expires_at) == (NOW, NOW + 1 * DIA)  # A encerrado na hora da troca p/ B
    assert (b.starts_at, b.expires_at) == (NOW + 1 * DIA, NOW + 3 * DIA)  # B encerrado na troca p/ C
    assert (c.starts_at, c.expires_at) == (NOW + 3 * DIA, NOW + 3 * DIA + 30 * DIA)  # C começa já
    assert find_overlaps([a, b, c]) == []


def test_upgrade_com_acesso_futuro_ja_empilhado_adia_o_futuro_sem_perder_dias(factory, session):
    """Caso raro, mas possível (ex.: um acesso futuro empilhado antes da troca imediata existir, ou
    de um realinhamento de reembolso anterior): o upgrade troca o VIGENTE na hora, e o acesso
    FUTURO que já estava empilhado depois dele não é descartado nem sobreposto -- só adiado pra
    depois do novo acesso (entitlement_chain.realign_after), preservando sua duração."""
    usuario = factory.user()
    a = raw_entitlement(factory, usuario, plan_code="weekly", starts_at=NOW - 1 * DIA, expires_at=NOW + 6 * DIA)
    c = raw_entitlement(factory, usuario, plan_code="weekly", starts_at=NOW + 6 * DIA, expires_at=NOW + 13 * DIA)
    b = give(factory, session, usuario, "monthly", now=NOW)  # upgrade DURANTE A, com C já empilhado
    a, b, c = _reload(session, a, b, c)

    assert (a.starts_at, a.expires_at) == (NOW - 1 * DIA, NOW)  # A encerrado na hora da troca
    assert (b.starts_at, b.expires_at) == (NOW, NOW + 30 * DIA)  # B (novo) começa já
    assert (c.starts_at, c.expires_at) == (NOW + 30 * DIA, NOW + 37 * DIA)  # C adiado pra depois de B
    assert find_overlaps([a, b, c]) == []
    assert c.duration_days == 7  # duração do futuro preservada, só a data mudou


def test_acesso_anterior_expirado_nao_entra_na_cadeia(factory, session):
    usuario = factory.user()
    raw_entitlement(factory, usuario, plan_code="weekly", starts_at=NOW - 10 * DIA, expires_at=NOW - 3 * DIA)
    novo = give(factory, session, usuario, "weekly")
    session.refresh(novo)
    assert novo.starts_at == NOW


def test_acesso_anterior_revogado_nao_entra_na_cadeia(factory, session):
    usuario = factory.user()
    raw_entitlement(
        factory, usuario, plan_code="monthly", starts_at=NOW - 1 * DIA, expires_at=NOW + 29 * DIA,
        status="revoked", revoked_at=NOW,
    )
    novo = give(factory, session, usuario, "weekly")
    session.refresh(novo)
    assert novo.starts_at == NOW


def test_conceder_o_mesmo_pagamento_duas_vezes_devolve_o_mesmo_acesso(factory, session):
    usuario = factory.user()
    pagamento = factory.payment(usuario, plan_code="weekly", status="approved")
    primeiro = grant_entitlement(session, user_id=usuario.id, payment_id=pagamento.id, plan_code="weekly", now=NOW)
    segundo = grant_entitlement(session, user_id=usuario.id, payment_id=pagamento.id, plan_code="weekly", now=NOW + 2 * DIA)
    assert primeiro.id == segundo.id
    assert len(_todos(session, usuario)) == 1  # sem empilhar de novo


def test_pagamento_de_outro_usuario_e_recusado(factory, session):
    dono, outro = factory.user(), factory.user()
    pagamento = factory.payment(dono, plan_code="weekly", status="approved")
    with pytest.raises(InvalidEntitlementRequestError):
        grant_entitlement(session, user_id=outro.id, payment_id=pagamento.id, plan_code="weekly", now=NOW)
    assert _todos(session, outro) == []


def test_free_e_plano_desconhecido_e_usuario_inexistente(factory, session):
    usuario = factory.user()
    pagamento = factory.payment(usuario, status="approved")
    with pytest.raises(InvalidEntitlementRequestError):
        grant_entitlement(session, user_id=usuario.id, payment_id=pagamento.id, plan_code="free", now=NOW)
    with pytest.raises(UnknownPlanError):
        grant_entitlement(session, user_id=usuario.id, payment_id=pagamento.id, plan_code="premium", now=NOW)
    with pytest.raises(UserNotFoundError):
        grant_entitlement(session, user_id=999_999, payment_id=pagamento.id, plan_code="weekly", now=NOW)
    assert _todos(session, usuario) == []


def test_compute_next_window_e_so_leitura(factory, session):
    usuario = factory.user()
    give(factory, session, usuario, "weekly")
    inicio, fim = compute_next_window(session, usuario.id, 30, now=NOW + DIA)
    assert inicio == NOW + 7 * DIA and fim == inicio + 30 * DIA
    assert len(_todos(session, usuario)) == 1


# ============================================================================ acesso vigente
def test_sem_entitlement_nao_ha_acesso_vigente(factory, session):
    assert get_current_entitlement(session, factory.user().id, now=NOW) is None


def test_entitlement_ativo_e_o_vigente(factory, session):
    usuario = factory.user()
    acesso = give(factory, session, usuario, "monthly")
    assert get_current_entitlement(session, usuario.id, now=NOW + 3 * DIA).id == acesso.id


def test_expirado_futuro_e_revogado_nao_sao_vigentes(factory, session):
    usuario = factory.user()
    raw_entitlement(factory, usuario, plan_code="weekly", starts_at=NOW - 10 * DIA, expires_at=NOW - 3 * DIA)
    raw_entitlement(factory, usuario, plan_code="weekly", starts_at=NOW + 2 * DIA, expires_at=NOW + 9 * DIA)
    raw_entitlement(
        factory, usuario, plan_code="monthly", starts_at=NOW - 1 * DIA, expires_at=NOW + 29 * DIA,
        status="revoked", revoked_at=NOW - DIA / 2,
    )
    assert get_current_entitlement(session, usuario.id, now=NOW) is None


def test_limites_do_intervalo_inicio_incluso_fim_excluso(factory, session):
    usuario = factory.user()
    acesso = raw_entitlement(factory, usuario, plan_code="weekly", starts_at=NOW, expires_at=NOW + 7 * DIA)
    assert get_current_entitlement(session, usuario.id, now=NOW - DIA / 1000) is None
    assert get_current_entitlement(session, usuario.id, now=NOW).id == acesso.id
    assert get_current_entitlement(session, usuario.id, now=NOW + 7 * DIA - DIA / 1000).id == acesso.id
    assert get_current_entitlement(session, usuario.id, now=NOW + 7 * DIA) is None


def test_na_troca_exata_de_acessos_so_um_esta_vigente(factory, session):
    usuario = factory.user()
    a = give(factory, session, usuario, "weekly")
    b = give(factory, session, usuario, "monthly")
    assert get_current_entitlement(session, usuario.id, now=NOW + 7 * DIA - DIA / 1000).id == a.id
    assert get_current_entitlement(session, usuario.id, now=NOW + 7 * DIA).id == b.id


# ============================================================================ inconsistência (sobreposição)
def test_dois_acessos_vigentes_sobrepostos_sao_inconsistencia_e_nao_ha_plano_vencedor(factory, session, caplog):
    usuario = factory.user()
    a = raw_entitlement(factory, usuario, plan_code="weekly", starts_at=NOW - 1 * DIA, expires_at=NOW + 6 * DIA)
    b = raw_entitlement(factory, usuario, plan_code="vip_batch", starts_at=NOW - 2 * DIA, expires_at=NOW + 28 * DIA)
    with caplog.at_level(logging.ERROR, logger="minhoca"):
        with pytest.raises(EntitlementInconsistencyError) as erro:
            get_current_entitlement(session, usuario.id, now=NOW)
    assert erro.value.user_id == usuario.id
    assert set(erro.value.entitlement_ids) == {a.id, b.id}  # nenhum é "escolhido"
    assert "INCONSISTÊNCIA" in caplog.text


def test_sobreposicao_entre_vigente_e_futuro_tambem_bloqueia(factory, session):
    usuario = factory.user()
    raw_entitlement(factory, usuario, plan_code="weekly", starts_at=NOW - 1 * DIA, expires_at=NOW + 6 * DIA)
    raw_entitlement(factory, usuario, plan_code="monthly", starts_at=NOW + 3 * DIA, expires_at=NOW + 33 * DIA)
    with pytest.raises(EntitlementInconsistencyError):
        get_current_entitlement(session, usuario.id, now=NOW)


def test_sobreposicao_so_entre_futuros_nao_bloqueia_hoje_mas_e_registrada(factory, session, caplog):
    usuario = factory.user()
    atual = raw_entitlement(factory, usuario, plan_code="weekly", starts_at=NOW - 1 * DIA, expires_at=NOW + 6 * DIA)
    raw_entitlement(factory, usuario, plan_code="monthly", starts_at=NOW + 6 * DIA, expires_at=NOW + 36 * DIA)
    raw_entitlement(factory, usuario, plan_code="weekly", starts_at=NOW + 20 * DIA, expires_at=NOW + 27 * DIA)
    with caplog.at_level(logging.ERROR, logger="minhoca"):
        assert get_current_entitlement(session, usuario.id, now=NOW).id == atual.id
    assert "INCONSISTÊNCIA" in caplog.text


def test_nao_se_empilha_sobre_uma_cadeia_inconsistente(factory, session):
    usuario = factory.user()
    raw_entitlement(factory, usuario, plan_code="weekly", starts_at=NOW - 1 * DIA, expires_at=NOW + 6 * DIA)
    raw_entitlement(factory, usuario, plan_code="weekly", starts_at=NOW + 5 * DIA, expires_at=NOW + 12 * DIA)
    antes = len(_todos(session, usuario))
    with pytest.raises(EntitlementInconsistencyError):
        give(factory, session, usuario, "monthly")
    assert len(_todos(session, usuario)) == antes  # nada foi criado


# ============================================================================ revogação e realinhamento
def _cadeia(factory, usuario):
    """A (vigente, semanal) -> B (semanal) -> C (mensal) -> D (semanal), colados."""
    a = raw_entitlement(factory, usuario, plan_code="weekly", starts_at=NOW - 2 * DIA, expires_at=NOW + 5 * DIA)
    b = raw_entitlement(factory, usuario, plan_code="weekly", starts_at=NOW + 5 * DIA, expires_at=NOW + 12 * DIA)
    c = raw_entitlement(factory, usuario, plan_code="monthly", starts_at=NOW + 12 * DIA, expires_at=NOW + 42 * DIA)
    d = raw_entitlement(factory, usuario, plan_code="weekly", starts_at=NOW + 42 * DIA, expires_at=NOW + 49 * DIA)
    return a, b, c, d


def _reload(session, *acessos):
    session.expire_all()
    return [session.get(Entitlement, a.id) for a in acessos]


def test_revogar_o_vigente_faz_o_proximo_comecar_agora(factory, session):
    usuario = factory.user()
    a, b, c, d = _cadeia(factory, usuario)
    resultado = revoke_entitlement(session, entitlement_id=a.id, reason="refunded", now=NOW)
    a, b, c, d = _reload(session, a, b, c, d)
    assert a.status == "revoked" and a.revoked_at == NOW and a.revoke_reason == "refunded"
    assert (b.starts_at, b.expires_at) == (NOW, NOW + 7 * DIA)
    assert (c.starts_at, c.expires_at) == (NOW + 7 * DIA, NOW + 37 * DIA)
    assert (d.starts_at, d.expires_at) == (NOW + 37 * DIA, NOW + 44 * DIA)
    assert resultado.realigned_ids == (b.id, c.id, d.id) and not resultado.inconsistent


def test_revogar_um_futuro_puxa_os_seguintes_e_nao_move_o_vigente(factory, session):
    usuario = factory.user()
    a, b, c, d = _cadeia(factory, usuario)
    revoke_entitlement(session, entitlement_id=b.id, reason="chargeback", now=NOW)
    a, b, c, d = _reload(session, a, b, c, d)
    assert (a.starts_at, a.expires_at) == (NOW - 2 * DIA, NOW + 5 * DIA)  # vigente intacto
    assert b.status == "revoked"
    assert (c.starts_at, c.expires_at) == (NOW + 5 * DIA, NOW + 35 * DIA)  # colado ao fim do vigente
    assert (d.starts_at, d.expires_at) == (NOW + 35 * DIA, NOW + 42 * DIA)
    assert find_overlaps([a, c, d]) == []


def test_exemplo_aprovado_a_e_c_revogados_b_e_d_validos(factory, session):
    usuario = factory.user()
    a, b, c, d = _cadeia(factory, usuario)
    revoke_entitlement(session, entitlement_id=a.id, reason="refunded", now=NOW)
    revoke_entitlement(session, entitlement_id=c.id, reason="chargeback", now=NOW)
    a, b, c, d = _reload(session, a, b, c, d)
    assert (a.status, c.status) == ("revoked", "revoked")
    assert (b.starts_at, b.expires_at) == (NOW, NOW + 7 * DIA)  # B começa agora
    assert (d.starts_at, d.expires_at) == (NOW + 7 * DIA, NOW + 14 * DIA)  # D logo depois de B
    assert find_overlaps([b, d]) == []
    assert b.duration_days == 7 and d.duration_days == 7  # duração preservada


def test_revogado_nunca_ressuscita_nem_participa_do_realinhamento(factory, session):
    usuario = factory.user()
    a, b, c, d = _cadeia(factory, usuario)
    revoke_entitlement(session, entitlement_id=c.id, reason="refunded", now=NOW)
    (c_antes,) = _reload(session, c)
    janela_c = (c_antes.starts_at, c_antes.expires_at)
    revoke_entitlement(session, entitlement_id=b.id, reason="refunded", now=NOW)  # outro realinhamento depois
    a, b, c, d = _reload(session, a, b, c, d)
    assert c.status == "revoked" and c.revoked_at is not None
    assert (c.starts_at, c.expires_at) == janela_c  # a janela do revogado não foi mexida
    assert get_current_entitlement(session, usuario.id, now=NOW + 6 * DIA).id == d.id  # D foi puxado para +5d..+12d


def test_revogar_de_novo_e_idempotente(factory, session):
    usuario = factory.user()
    a, b, c, d = _cadeia(factory, usuario)
    revoke_entitlement(session, entitlement_id=b.id, reason="refunded", now=NOW)
    (b_antes,) = _reload(session, b)
    revogado_em = b_antes.revoked_at
    repetido = revoke_entitlement(session, entitlement_id=b.id, reason="chargeback", now=NOW + DIA)
    (b_depois,) = _reload(session, b)
    assert repetido.already_revoked and repetido.realigned_ids == ()
    assert b_depois.revoked_at == revogado_em and b_depois.revoke_reason == "refunded"  # nada mudou


def test_revogacao_sem_necessidade_de_realinhar(factory, session):
    usuario = factory.user()
    a = raw_entitlement(factory, usuario, plan_code="weekly", starts_at=NOW - 1 * DIA, expires_at=NOW + 6 * DIA)
    resultado = revoke_entitlement(session, entitlement_id=a.id, reason="refunded", now=NOW)
    assert resultado.realigned_ids == () and not resultado.inconsistent
    assert get_current_entitlement(session, usuario.id, now=NOW) is None


def test_revogacao_e_transacional_se_o_realinhamento_falhar_nada_muda(factory, session, monkeypatch):
    usuario = factory.user()
    a, b, c, d = _cadeia(factory, usuario)

    def falha(items, now):
        raise RuntimeError("falha simulada no realinhamento")

    monkeypatch.setattr(ent_service.chain, "realign_future", falha)
    with pytest.raises(RuntimeError):
        revoke_entitlement(session, entitlement_id=a.id, reason="refunded", now=NOW)
    a, b, c, d = _reload(session, a, b, c, d)
    assert a.status == "granted" and a.revoked_at is None  # a revogação foi desfeita junto
    assert (b.starts_at, c.starts_at, d.starts_at) == (NOW + 5 * DIA, NOW + 12 * DIA, NOW + 42 * DIA)


def test_com_sobreposicao_revoga_mas_nao_realinha_e_registra_erro(factory, session, caplog):
    usuario = factory.user()
    a = raw_entitlement(factory, usuario, plan_code="weekly", starts_at=NOW - 1 * DIA, expires_at=NOW + 6 * DIA)
    b = raw_entitlement(factory, usuario, plan_code="weekly", starts_at=NOW + 3 * DIA, expires_at=NOW + 10 * DIA)
    c = raw_entitlement(factory, usuario, plan_code="monthly", starts_at=NOW + 30 * DIA, expires_at=NOW + 60 * DIA)
    with caplog.at_level(logging.ERROR, logger="minhoca"):
        resultado = revoke_entitlement(session, entitlement_id=c.id, reason="refunded", now=NOW)
    assert resultado.inconsistent and resultado.realigned_ids == ()
    a, b, c = _reload(session, a, b, c)
    assert c.status == "revoked"  # o acesso reembolsado precisa acabar
    assert (b.starts_at, b.expires_at) == (NOW + 3 * DIA, NOW + 10 * DIA)  # nada foi movido às cegas
    assert "INCONSISTÊNCIA" in caplog.text


def test_revogar_acesso_ja_expirado_nao_mexe_em_ninguem(factory, session):
    usuario = factory.user()
    velho = raw_entitlement(factory, usuario, plan_code="weekly", starts_at=NOW - 20 * DIA, expires_at=NOW - 13 * DIA)
    atual = raw_entitlement(factory, usuario, plan_code="monthly", starts_at=NOW - 1 * DIA, expires_at=NOW + 29 * DIA)
    resultado = revoke_entitlement(session, entitlement_id=velho.id, reason="chargeback", now=NOW)
    assert resultado.realigned_ids == ()
    (atual,) = _reload(session, atual)
    assert atual.expires_at == NOW + 29 * DIA


def test_argumentos_invalidos_na_revogacao(factory, session):
    usuario = factory.user()
    a = raw_entitlement(factory, usuario, plan_code="weekly", starts_at=NOW, expires_at=NOW + 7 * DIA)
    for motivo in ("", "   ", None, "x" * 33):
        with pytest.raises(InvalidEntitlementRequestError):
            revoke_entitlement(session, entitlement_id=a.id, reason=motivo, now=NOW)
    with pytest.raises(EntitlementNotFoundError):
        revoke_entitlement(session, entitlement_id=999_999, reason="refunded", now=NOW)
    (a,) = _reload(session, a)
    assert a.status == "granted"
