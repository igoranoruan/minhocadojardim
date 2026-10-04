from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base, sql_in
from database.types import UTCDateTime, utcnow

REFERRAL_STATUSES = ("pending_plan", "applied")


class Referral(Base):
    """Livro-razão do programa de indicação (Etapa 8 da revisão de UX, aprovação do CÉREBRO):
    mesma filosofia "ledger, não contador" de `generations` -- uma linha por indicação
    RECOMPENSADA, nunca uma por clique/visita/cadastro.

    Criada só no momento em que o indicado faz seu PRIMEIRO pagamento aprovado (nunca no
    cadastro/login, que é grátis e fácil de simular com contas falsas) -- ver
    services/referrals.py, único módulo do projeto que escreve nesta tabela.
    `referred_user_id` é UNIQUE: cada pessoa só pode gerar uma recompensa de indicação uma única
    vez na vida, não importa quantos pagamentos faça depois.

    status:
    - "pending_plan": o indicador não tinha um plano pago vigente no momento em que a indicação
      converteu -- os `reward_days` ficam guardados aqui até a próxima vez que o próprio indicador
      receber um entitlement nosso (ver services/referrals.py::apply_pending_rewards_for_referrer),
      aplicados automaticamente, sem fila/cron (mesma filosofia "oportunista" já usada em
      services/usage.py::fail_stale_reservations).
    - "applied": os `reward_days` já foram somados a um entitlement do indicador --
      applied_entitlement_id/applied_at registram quando e em qual.
    """

    __tablename__ = "referrals"
    __table_args__ = (
        UniqueConstraint("referred_user_id", name="uq_referrals_referred_user_id"),
        CheckConstraint("reward_days > 0", name="ck_referrals_reward_days_positivo"),
        CheckConstraint(sql_in("status", REFERRAL_STATUSES), name="ck_referrals_status_valido"),
        CheckConstraint(
            "(status = 'pending_plan' AND applied_at IS NULL AND applied_entitlement_id IS NULL) "
            "OR (status = 'applied' AND applied_at IS NOT NULL AND applied_entitlement_id IS NOT NULL)",
            name="ck_referrals_aplicacao_consistente",
        ),
        CheckConstraint("referrer_user_id != referred_user_id", name="ck_referrals_nao_autoindicacao"),
        Index("ix_referrals_referrer_user_id_status", "referrer_user_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    referrer_user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", name="fk_referrals_referrer_user_id_users", ondelete="RESTRICT"),
        nullable=False,
    )
    referred_user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", name="fk_referrals_referred_user_id_users", ondelete="RESTRICT"),
        nullable=False,
    )
    reward_days: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending_plan")
    applied_entitlement_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey(
            "entitlements.id", name="fk_referrals_applied_entitlement_id_entitlements", ondelete="RESTRICT"
        ),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    applied_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
