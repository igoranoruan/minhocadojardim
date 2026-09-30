"""Identidade anônima (visitante Free sem login): 1 User "placeholder" por dispositivo.

Motivação (revisão do CÉREBRO, Free anônimo): o requisito de produto é permitir 5 gerações/semana
sem exigir login. Toda a cadeia de cota/ownership (services/usage.py, services/entitlements.py,
services/locks.py, download por Generation.user_id) já é construída sobre um `user_id` real de uma
linha de `users` -- e deve CONTINUAR assim, sem duplicar essa lógica para um segundo conceito de
identidade. Por isso um visitante anônimo ganha uma linha REAL em `users` (criada automaticamente,
nunca por e-mail informado por ele), e esta tabela só guarda o mapeamento cookie -> esse user_id.

`users.email` continua NOT NULL e único (nenhuma alteração na tabela `users` -- decisão do
CÉREBRO): o placeholder recebe um e-mail sintético e nunca-entregável em `@device.invalid`
(domínio reservado pela RFC 2606, nunca roteável/resolvível), com um sufixo aleatório -- nunca
usado para enviar e-mail algum (ver services/anon_identity.py).

device_token_hash é o SHA-256 do token do cookie (mesmo padrão de hash_session_token em
utils/security.py -- o token já tem alta entropia, dispensa segredo adicional); o token em claro
nunca é persistido, só o cookie do navegador o guarda.

Relação 1:1 com `users` (UniqueConstraint em user_id): um usuário-placeholder nunca tem mais de um
dispositivo associado -- se precisasse, seria um novo usuário-placeholder (novo device_token),
nunca uma segunda linha aqui para o mesmo user_id.
"""
from datetime import datetime

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base
from database.types import UTCDateTime, utcnow


class AnonymousIdentity(Base):
    __tablename__ = "anonymous_identities"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_anonymous_identities_user_id"),
        UniqueConstraint("device_token_hash", name="uq_anonymous_identities_device_token_hash"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", name="fk_anonymous_identities_user_id_users", ondelete="RESTRICT"),
        nullable=False,
    )
    device_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    # Atualizado com debounce (mesmo padrão de AuthSession.last_used_at em services/auth.py) --
    # nunca uma escrita a cada requisição, só quando estiver mais velho que o intervalo configurado.
    last_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
