import hashlib
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import insert, text

from database.models import Payment, User
from database.types import utcnow


# ---------------------------------------------------------------- users / e-mail
def test_email_normalizado_com_strip_e_lower_antes_de_persistir(session, factory):
    user = factory.user(email="  Foo.Bar+Tag@Example.COM \n")
    session.refresh(user)
    assert user.email == "foo.bar+tag@example.com"  # pontos e +tag são preservados (sem regra de Gmail)
    guardado = session.execute(text("SELECT email FROM users WHERE id = :i"), {"i": user.id}).scalar_one()
    assert guardado == "foo.bar+tag@example.com"


def test_email_duplicado_e_rejeitado(factory, assert_rejected):
    factory.user(email="Ana@Example.com")
    assert_rejected(lambda: factory.user(email="  ana@example.COM "))


def test_check_do_email_rejeita_valor_fora_do_padrao(session, assert_rejected):
    def insere_direto(email: str) -> None:  # Core ignora o validador do ORM: só o CHECK protege
        session.execute(insert(User.__table__).values(email=email))
        session.commit()

    assert_rejected(lambda: insere_direto("Foo@Bar.com"))
    assert_rejected(lambda: insere_direto(" foo@bar.com"))
    insere_direto("foo@bar.com")  # controle: o valor normalizado passa


# ---------------------------------------------------------------- payments
def test_amount_cents_aceita_somente_inteiro(factory, session):
    for invalido in (9.9, 990.0, "990", True, Decimal("9.90")):
        with pytest.raises(ValueError):
            Payment(amount_cents=invalido)
    pagamento = factory.payment(amount_cents=990)
    assert pagamento.amount_cents == 990
    tipo = session.execute(
        text("SELECT typeof(amount_cents) FROM payments WHERE id = :i"), {"i": pagamento.id}
    ).scalar_one()
    assert tipo == "integer"


@pytest.mark.parametrize("valor", [0, -1])
def test_amount_cents_deve_ser_positivo(factory, assert_rejected, valor):
    assert_rejected(lambda: factory.payment(amount_cents=valor))


def test_mp_payment_id_e_unico_quando_presente(factory, assert_rejected):
    factory.payment(mp_payment_id=None)
    factory.payment(mp_payment_id=None)  # vários pagamentos ainda sem id do Mercado Pago: permitido
    factory.payment(mp_payment_id="111")
    assert_rejected(lambda: factory.payment(mp_payment_id="111"))


def test_external_reference_e_unica(factory, assert_rejected):
    factory.payment(external_reference="ref-unica")
    assert_rejected(lambda: factory.payment(external_reference="ref-unica"))


def test_metodos_de_pagamento_pix_e_cartao(factory, assert_rejected):
    factory.payment(method="pix")
    factory.payment(method="credit_card")
    assert_rejected(lambda: factory.payment(method="boleto"))


# ---------------------------------------------------------------- entitlements
def test_entitlement_payment_id_e_unico(factory, assert_rejected):
    pagamento = factory.payment()
    factory.entitlement(pagamento)
    assert_rejected(lambda: factory.entitlement(pagamento))


def test_expires_at_deve_ser_maior_que_starts_at(factory, assert_rejected):
    inicio = utcnow()
    factory.entitlement(starts_at=inicio, expires_at=inicio + timedelta(days=7))
    assert_rejected(lambda: factory.entitlement(starts_at=inicio, expires_at=inicio))
    assert_rejected(lambda: factory.entitlement(starts_at=inicio, expires_at=inicio - timedelta(seconds=1)))


def test_duration_days_deve_ser_positivo(factory, assert_rejected):
    assert_rejected(lambda: factory.entitlement(duration_days=0))


def test_revogacao_precisa_ser_consistente(factory, assert_rejected):
    assert_rejected(lambda: factory.entitlement(status="revoked", revoked_at=None))
    assert_rejected(lambda: factory.entitlement(status="granted", revoked_at=utcnow()))
    factory.entitlement(status="revoked", revoked_at=utcnow(), revoke_reason="refunded")


# ---------------------------------------------------------------- batches
def test_batch_item_count_minimo_1(factory, assert_rejected):
    assert_rejected(lambda: factory.batch(item_count=0))
    factory.batch(item_count=1)
    factory.batch(item_count=10)
    # O máximo de 10 NÃO é regra do banco: é responsabilidade do serviço (config.py).
    factory.batch(item_count=11)


def test_batch_request_id_unico_por_usuario(factory, assert_rejected):
    usuario = factory.user()
    outro = factory.user()
    factory.batch(usuario, request_id="lote-1")
    factory.batch(outro, request_id="lote-1")  # outro usuário pode repetir o mesmo request_id
    assert_rejected(lambda: factory.batch(usuario, request_id="lote-1"))


# ---------------------------------------------------------------- generations
def test_generation_avulsa_valida(factory):
    factory.generation(batch_id=None, position=None, request_id="req-a")


def test_generation_de_lote_valida(factory):
    lote = factory.batch()
    factory.generation(factory.user(), batch_id=lote.id, position=1, request_id=None)


def test_generation_estrutura_avulsa_ou_lote_e_imposta(factory, assert_rejected):
    lote = factory.batch()
    assert_rejected(lambda: factory.generation(request_id=None))  # avulsa sem request_id
    assert_rejected(lambda: factory.generation(batch_id=lote.id, position=None, request_id=None))  # lote sem position
    assert_rejected(lambda: factory.generation(batch_id=lote.id, position=1, request_id="req-x"))  # lote com request_id
    assert_rejected(lambda: factory.generation(batch_id=None, position=2, request_id="req-y"))  # position sem lote
    assert_rejected(lambda: factory.generation(batch_id=lote.id, position=0, request_id=None))  # position < 1


def test_generation_request_id_garante_idempotencia_por_usuario(factory, assert_rejected):
    usuario = factory.user()
    outro = factory.user()
    factory.generation(usuario, request_id="clique-1")
    factory.generation(outro, request_id="clique-1")  # outro usuário: permitido
    assert_rejected(lambda: factory.generation(usuario, request_id="clique-1"))  # duplo clique / retry


def test_generation_posicao_unica_por_lote(factory, assert_rejected):
    usuario = factory.user()
    lote = factory.batch(usuario)
    factory.generation(usuario, batch_id=lote.id, position=1, request_id=None)
    factory.generation(usuario, batch_id=lote.id, position=2, request_id=None)  # vários NULL em request_id: ok
    assert_rejected(lambda: factory.generation(usuario, batch_id=lote.id, position=1, request_id=None))


def test_output_sha256_aceita_null_ate_a_conclusao(factory, session):
    geracao = factory.generation()
    session.refresh(geracao)
    assert geracao.output_sha256 is None


def test_output_sha256_comporta_64_caracteres(factory, session, assert_rejected):
    digest = hashlib.sha256(b"arquivo final").hexdigest()
    assert len(digest) == 64
    geracao = factory.generation()
    geracao.output_sha256 = digest
    geracao.status = "completed"
    geracao.finished_at = utcnow()
    session.commit()
    session.refresh(geracao)
    assert geracao.output_sha256 == digest

    assert_rejected(lambda: factory.generation(output_sha256="a" * 63))
    assert_rejected(lambda: factory.generation(output_sha256="a" * 65))


# ---------------------------------------------------------------- status inválido
@pytest.mark.parametrize(
    "criar",
    [
        lambda f: f.payment(status="paid"),
        lambda f: f.entitlement(status="active"),
        lambda f: f.generation(status="done"),
    ],
    ids=["payments", "entitlements", "generations"],
)
def test_status_invalido_e_rejeitado(factory, assert_rejected, criar):
    assert_rejected(lambda: criar(factory))


def test_status_validos_sao_aceitos(factory):
    for status in ("pending", "approved", "rejected", "cancelled", "refunded", "charged_back"):
        factory.payment(status=status)
    for status in ("reserved", "completed", "failed"):
        factory.generation(status=status)
