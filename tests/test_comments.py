"""services/comments.py::create_comment -- nenhuma suíte cobria este módulo antes da ETAPA B7
(teste VIP de 24h, pedido e aprovação do Igor, 29/10/2026). Criado agora porque a condição 1 da
aprovação do Igor pediu explicitamente um levantamento de "todos os usos de .paid, paid_plans() e
'tem plano pago'" -- este módulo é um dos achados: create_comment exigia só "tem ENTITLEMENT
vigente", o que deixaria quem está no trial_vip publicar um comentário com a mesma aparência de
depoimento de cliente pagante. Corrigido para excluir explicitamente plan_code == TRIAL_VIP; esta
suíte prova a correção (e cobre o comportamento básico do módulo, nunca testado antes)."""
from datetime import timedelta

import pytest

from helpers_usage import NOW, give
from services.comments import InvalidCommentError, NoActivePlanError, create_comment

DIA = timedelta(days=1)


def test_usuario_free_sem_entitlement_e_recusado(factory, session):
    usuario = factory.user()
    with pytest.raises(NoActivePlanError):
        create_comment(session, user_id=usuario.id, author_name="Fulano", body="Muito bom!")


def test_usuario_com_trial_vip_vigente_e_recusado(factory, session):
    """Condição 1 da aprovação do Igor: teste dá acesso às ferramentas, mas não pode "parecer"
    cliente pagante -- aqui, não pode comentar."""
    usuario = factory.user()
    factory.trial_entitlement(usuario, starts_at=NOW, expires_at=NOW + DIA)
    with pytest.raises(NoActivePlanError):
        create_comment(session, user_id=usuario.id, author_name="Fulano", body="Muito bom!")


def test_usuario_com_plano_pago_vigente_pode_comentar(factory, session):
    usuario = factory.user()
    give(factory, session, usuario, "weekly", now=NOW)
    comentario = create_comment(session, user_id=usuario.id, author_name="Fulano", body="Muito bom!")
    assert comentario.id is not None
    assert comentario.approved is False  # nasce na fila de moderação
    assert comentario.is_example is False


def test_usuario_com_plano_pago_ja_expirado_e_recusado(factory, session):
    """"Vigente agora", não "já teve algum dia" -- mesma régua de get_current_entitlement."""
    usuario = factory.user()
    give(factory, session, usuario, "weekly", now=NOW - 30 * DIA)
    with pytest.raises(NoActivePlanError):
        create_comment(session, user_id=usuario.id, author_name="Fulano", body="Muito bom!")


def test_nome_vazio_e_recusado(factory, session):
    usuario = factory.user()
    give(factory, session, usuario, "weekly", now=NOW)
    with pytest.raises(InvalidCommentError):
        create_comment(session, user_id=usuario.id, author_name="   ", body="Muito bom!")


def test_corpo_vazio_e_recusado(factory, session):
    usuario = factory.user()
    give(factory, session, usuario, "weekly", now=NOW)
    with pytest.raises(InvalidCommentError):
        create_comment(session, user_id=usuario.id, author_name="Fulano", body="   ")
