"""services/comments.py::create_comment -- nenhuma suíte cobria este módulo antes da ETAPA B7
(teste VIP de 24h, pedido e aprovação do Igor, 29/10/2026). Criado agora porque a condição 1 da
aprovação do Igor pediu explicitamente um levantamento de "todos os usos de .paid, paid_plans() e
'tem plano pago'" -- este módulo é um dos achados: create_comment exigia só "tem ENTITLEMENT
vigente", o que deixaria quem está no trial_vip publicar um comentário com a mesma aparência de
depoimento de cliente pagante. Corrigido para excluir explicitamente plan_code == TRIAL_VIP; esta
suíte prova a correção (e cobre o comportamento básico do módulo, nunca testado antes).

create_comment (diferente de reserve_generation/grant_entitlement) NÃO aceita um `now` injetado --
sempre usa o relógio real (get_current_entitlement(db, user_id), sem `now`). Por isso os
entitlements "vigentes" aqui são montados em torno de utils.time_sp.resolve_now() (relógio real no
momento em que o teste roda), nunca da constante congelada NOW de helpers_usage.py (23/09/2026,
usada pelas outras suítes de uso/cota -- aqui deixaria o entitlement já expirado há muito tempo)."""
from datetime import timedelta

import pytest

from services.comments import InvalidCommentError, NoActivePlanError, create_comment
from services.entitlements import grant_entitlement
from utils.time_sp import resolve_now

DIA = timedelta(days=1)
AGORA = resolve_now()


def _da_plano_pago(factory, session, usuario, plan_code="weekly", now=AGORA):
    pagamento = factory.payment(usuario, plan_code=plan_code, status="approved")
    return grant_entitlement(session, user_id=usuario.id, payment_id=pagamento.id, plan_code=plan_code, now=now)


def test_usuario_free_sem_entitlement_e_recusado(factory, session):
    usuario = factory.user()
    with pytest.raises(NoActivePlanError):
        create_comment(session, user_id=usuario.id, author_name="Fulano", body="Muito bom!")


def test_usuario_com_trial_vip_vigente_e_recusado(factory, session):
    """Condição 1 da aprovação do Igor: teste dá acesso às ferramentas, mas não pode "parecer"
    cliente pagante -- aqui, não pode comentar."""
    usuario = factory.user()
    factory.trial_entitlement(usuario, starts_at=AGORA, expires_at=AGORA + DIA)
    with pytest.raises(NoActivePlanError):
        create_comment(session, user_id=usuario.id, author_name="Fulano", body="Muito bom!")


def test_usuario_com_plano_pago_vigente_pode_comentar(factory, session):
    usuario = factory.user()
    _da_plano_pago(factory, session, usuario)
    comentario = create_comment(session, user_id=usuario.id, author_name="Fulano", body="Muito bom!")
    assert comentario.id is not None
    assert comentario.approved is False  # nasce na fila de moderação
    assert comentario.is_example is False


def test_usuario_com_plano_pago_ja_expirado_e_recusado(factory, session):
    """"Vigente agora", não "já teve algum dia" -- mesma régua de get_current_entitlement."""
    usuario = factory.user()
    _da_plano_pago(factory, session, usuario, now=AGORA - 30 * DIA)
    with pytest.raises(NoActivePlanError):
        create_comment(session, user_id=usuario.id, author_name="Fulano", body="Muito bom!")


def test_nome_vazio_e_recusado(factory, session):
    usuario = factory.user()
    _da_plano_pago(factory, session, usuario)
    with pytest.raises(InvalidCommentError):
        create_comment(session, user_id=usuario.id, author_name="   ", body="Muito bom!")


def test_corpo_vazio_e_recusado(factory, session):
    usuario = factory.user()
    _da_plano_pago(factory, session, usuario)
    with pytest.raises(InvalidCommentError):
        create_comment(session, user_id=usuario.id, author_name="Fulano", body="   ")
