from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base
from database.types import UTCDateTime, utcnow


class TrialRedemption(Base):
    """Ledger de resgate do "Teste VIP de 24h" (ETAPA B7, link de campanha, pedido do Igor,
    29/10/2026) -- NÃO é uma segunda fonte de acesso (quem decide o acesso continua sendo só
    `entitlements`, ver database/models/entitlement.py::TRIAL_VIP_PLAN_CODE): esta tabela existe
    só para as travas de abuso e para a medição (seção 5 do pedido, etapa seguinte), do mesmo jeito
    que `generations`/`batches` já são ledgers ao lado de `entitlements`, nunca um sistema paralelo
    de acesso.

    `entitlement_id` é UNIQUE: exatamente 1 resgate por entitlement de teste (e, por construção de
    services/trial.py, exatamente 1 entitlement de teste por usuário na vida -- "1 teste por
    conta, para sempre" é garantido pela MESMA checagem que já impede empilhar com plano pago,
    não por uma unicidade separada aqui).

    IP e identificador de aparelho NUNCA gravados crus -- só o hash (HMAC-SHA256 com
    settings.ip_hash_secret, ver utils.security.hash_ip/hash_device_id): mesma régua de privacidade
    que login_codes.ip_hash já segue desde a Etapa 3. `ip_hash` é NULLABLE pelo MESMO motivo de
    login_codes.ip_hash: `request.client.host` pode vir None (ver routes/deps.py::client_ip) --
    nesse caso a trava de "no máximo 2 por IP" simplesmente não se aplica a esta linha, nunca um
    hash de string vazia."""

    __tablename__ = "trial_redemptions"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_trial_redemptions_user_id"),
        UniqueConstraint("entitlement_id", name="uq_trial_redemptions_entitlement_id"),
        CheckConstraint("campaign != ''", name="ck_trial_redemptions_campaign_nao_vazia"),
        Index("ix_trial_redemptions_device_hash", "device_hash"),
        Index("ix_trial_redemptions_ip_hash_created_at", "ip_hash", "created_at"),
        Index("ix_trial_redemptions_campaign", "campaign"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", name="fk_trial_redemptions_user_id_users", ondelete="RESTRICT"),
        nullable=False,
    )
    entitlement_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey(
            "entitlements.id", name="fk_trial_redemptions_entitlement_id_entitlements", ondelete="RESTRICT"
        ),
        nullable=False,
    )
    campaign: Mapped[str] = mapped_column(String(40), nullable=False)
    ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    device_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
