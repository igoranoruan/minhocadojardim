from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base, sql_in
from database.types import UTCDateTime, utcnow

ENTITLEMENT_STATUSES = ("granted", "revoked")


TRIAL_VIP_PLAN_CODE = "trial_vip"  # services/plans.py é a fonte do catálogo; reexportado aqui só
# para o CHECK constraint abaixo não duplicar o literal e para services/entitlements.py poder
# importar de um único lugar neutro (sem criar um import circular com services/plans.py).


class Entitlement(Base):
    """Período de acesso. Normalmente gerado por um pagamento (payment_id obrigatório, um
    pagamento gera no máximo um acesso -- UNIQUE). ETAPA B7 (teste VIP de 24h por link de
    campanha, pedido do Igor, 29/10/2026): a ÚNICA exceção é `plan_code='trial_vip'`, concedido
    por services/entitlements.py::grant_trial_entitlement SEM nenhum Payment -- payment_id fica
    NULL, e o CHECK abaixo garante que nenhuma outra combinação é possível (todo entitlement de
    qualquer outro plano continua exigindo payment_id, exatamente como antes desta etapa). Nenhuma
    migração/coluna de payments foi tocada -- só esta tabela, e só para permitir NULL aqui.

    Reaproveitar esta MESMA tabela para o teste (em vez de uma tabela de acesso paralela) foi uma
    decisão explícita (proposta aprovada pelo Igor, 29/10/2026): ganha de graça a checagem de
    sobreposição (get_current_entitlement), o "não empilha com plano pago" (basta recusar o
    resgate se já existir QUALQUER linha aqui para o usuário -- também cobre "quem já pagou não
    pode", já que pagar sempre grava uma linha aqui) e a troca imediata ao fazer um upgrade de
    verdade durante o teste (grant_entitlement já trata isso, sem mudança nenhuma).

    Esta etapa só cria a persistência: empilhamento, realinhamento, revogação e o
    cálculo do acesso vigente são regras de serviço de etapas posteriores.
    """

    __tablename__ = "entitlements"
    __table_args__ = (
        UniqueConstraint("payment_id", name="uq_entitlements_payment_id"),
        CheckConstraint("duration_days > 0", name="ck_entitlements_duration_days_positivo"),
        CheckConstraint("expires_at > starts_at", name="ck_entitlements_expires_at_maior_que_starts_at"),
        CheckConstraint(sql_in("status", ENTITLEMENT_STATUSES), name="ck_entitlements_status_valido"),
        CheckConstraint(
            "(status = 'granted' AND revoked_at IS NULL) OR (status = 'revoked' AND revoked_at IS NOT NULL)",
            name="ck_entitlements_revogacao_consistente",
        ),
        # ETAPA B7: payment_id só pode ser NULL para o plano de teste, e o plano de teste SEMPRE
        # tem payment_id NULL -- nunca os dois estados ao mesmo tempo para o mesmo plano_code.
        CheckConstraint(
            f"(plan_code = '{TRIAL_VIP_PLAN_CODE}' AND payment_id IS NULL) "
            f"OR (plan_code != '{TRIAL_VIP_PLAN_CODE}' AND payment_id IS NOT NULL)",
            name="ck_entitlements_payment_id_so_para_planos_pagos",
        ),
        Index("ix_entitlements_user_id_status_expires_at", "user_id", "status", "expires_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", name="fk_entitlements_user_id_users", ondelete="RESTRICT"),
        nullable=False,
    )
    plan_code: Mapped[str] = mapped_column(String(32), nullable=False)
    payment_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("payments.id", name="fk_entitlements_payment_id_payments", ondelete="RESTRICT"),
        nullable=True,
    )
    duration_days: Mapped[int] = mapped_column(Integer, nullable=False)
    starts_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="granted")
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    revoke_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utcnow, onupdate=utcnow
    )
