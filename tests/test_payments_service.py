"""payments/service.py::create_payment (Etapa 10.1): fundação interna de pagamentos -- cria um
Payment "pending" com preço sempre vindo do catálogo (services.plans), nunca do chamador.

Esta suíte NÃO cobre Mercado Pago, checkout, PIX real, cartão real, webhook ou concessão de
entitlement -- nada disso existe ainda (ver payments/__init__.py e payments/service.py). A
fronteira em si (nenhum desses imports acontece) é coberta por test_payments_architecture.py.
"""
from dataclasses import replace

import pytest
from sqlalchemy import func, select

from database.models import Payment
from helpers_usage import run_parallel
from payments.errors import InvalidPaymentMethodError, PlanNotPurchasableError, UnknownPlanError
from payments.service import create_payment
from services import plans as plans_module
from services.plans import WEEKLY_PLAN


def test_create_payment_feliz(factory, session):
    """Payment criado com os campos exatos previstos na especificação da Etapa 10.1."""
    usuario = factory.user()
    payment = create_payment(session, user_id=usuario.id, plan_code="weekly", method="pix")

    assert payment.id is not None
    assert payment.user_id == usuario.id
    assert payment.plan_code == "weekly"
    assert payment.amount_cents == WEEKLY_PLAN.price_cents
    assert payment.method == "pix"
    assert payment.status == "pending"
    assert payment.mp_payment_id is None
    assert payment.status_detail is None
    assert payment.approved_at is None
    assert payment.refunded_at is None
    assert payment.external_reference is not None


def test_preco_sempre_vem_do_catalogo_nao_de_um_valor_fixo(factory, session, monkeypatch):
    """Se o catálogo mudar o preço, o Payment criado reflete o NOVO preço -- nunca um valor
    hardcoded coincidente. Prova que create_payment lê o preço de verdade, em vez de repetir um
    número que só por acaso bateria com o catálogo de hoje."""
    usuario = factory.user()
    novo_preco = WEEKLY_PLAN.price_cents + 12345
    monkeypatch.setitem(plans_module.PLANS, "weekly", replace(WEEKLY_PLAN, price_cents=novo_preco))

    payment = create_payment(session, user_id=usuario.id, plan_code="weekly", method="pix")

    assert payment.amount_cents == novo_preco


def test_plano_inexistente_e_recusado(factory, session):
    usuario = factory.user()
    with pytest.raises(UnknownPlanError):
        create_payment(session, user_id=usuario.id, plan_code="plano-que-nao-existe", method="pix")


def test_plano_free_e_recusado(factory, session):
    usuario = factory.user()
    with pytest.raises(PlanNotPurchasableError):
        create_payment(session, user_id=usuario.id, plan_code="free", method="pix")


def test_metodo_invalido_e_recusado(factory, session):
    usuario = factory.user()
    with pytest.raises(InvalidPaymentMethodError):
        create_payment(session, user_id=usuario.id, plan_code="weekly", method="boleto")


def test_mesma_chamada_com_pending_ainda_aberto_devolve_o_mesmo_payment(factory, session):
    """Duplo-clique/retry: repetir user_id+plan_code+method enquanto o Payment anterior ainda
    está 'pending' NUNCA cria um segundo -- devolve exatamente o mesmo registro (mesmo id)."""
    usuario = factory.user()
    primeiro = create_payment(session, user_id=usuario.id, plan_code="weekly", method="pix")
    segundo = create_payment(session, user_id=usuario.id, plan_code="weekly", method="pix")

    assert segundo.id == primeiro.id
    assert segundo.external_reference == primeiro.external_reference
    total = session.execute(
        select(func.count()).select_from(Payment).where(Payment.user_id == usuario.id)
    ).scalar_one()
    assert total == 1


def test_payment_ja_aprovado_permite_nova_compra_do_mesmo_plano(factory, session):
    """Uma vez que o Payment anterior sai de 'pending' (aprovado, rejeitado, etc.), uma nova
    chamada idêntica cria um Payment NOVO -- comprar o mesmo plano de novo é uma operação
    legítima (entitlements se empilham), só não pode haver dois 'pending' idênticos ao mesmo
    tempo."""
    usuario = factory.user()
    primeiro = create_payment(session, user_id=usuario.id, plan_code="weekly", method="pix")
    primeiro.status = "approved"
    session.commit()

    segundo = create_payment(session, user_id=usuario.id, plan_code="weekly", method="pix")

    assert segundo.id != primeiro.id
    total = session.execute(
        select(func.count()).select_from(Payment).where(Payment.user_id == usuario.id)
    ).scalar_one()
    assert total == 2


def test_external_reference_presente_e_unica_entre_pagamentos_diferentes(factory, session):
    usuario = factory.user()
    primeiro = create_payment(session, user_id=usuario.id, plan_code="weekly", method="pix")
    primeiro.status = "approved"
    session.commit()
    segundo = create_payment(session, user_id=usuario.id, plan_code="monthly", method="credit_card")

    assert primeiro.external_reference
    assert segundo.external_reference
    assert primeiro.external_reference != segundo.external_reference
    assert len(primeiro.external_reference) == 32  # uuid4().hex


def test_concorrencia_duas_chamadas_identicas_nao_criam_dois_pending(factory, session, maker):
    """Mesma técnica de tests/test_usage_concurrency.py: N threads, cada uma com sua própria
    Session/conexão, largadas juntas (barrier) -- comprova que lock_user_row serializa a
    verificação+criação também para pagamentos, não só para cota."""
    usuario = factory.user()

    resultados = run_parallel(
        maker, 5,
        lambda s, i: create_payment(s, user_id=usuario.id, plan_code="weekly", method="pix").id,
    )
    ids = [valor for tipo, valor in resultados if tipo == "ok"]
    erros = [valor for tipo, valor in resultados if tipo == "erro"]

    assert not erros, erros
    assert len(set(ids)) == 1  # todas as 5 threads viram o MESMO Payment, nunca 5 diferentes

    total = session.execute(
        select(func.count()).select_from(Payment).where(Payment.user_id == usuario.id)
    ).scalar_one()
    assert total == 1
