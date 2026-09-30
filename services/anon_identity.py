"""Identidade anônima (Free sem login -- aprovação do CÉREBRO, Free anônimo).

Cria/reaproveita um `User` "placeholder" (e-mail sintético @device.invalid, nunca verificado) para
um visitante sem sessão -- essa é a identidade Free DO DISPOSITIVO. Nenhuma outra parte do projeto
(services/usage.py, services/entitlements.py, services/locks.py, download por Generation.user_id)
precisa saber que um usuário é "anônimo" -- para elas, é um user_id como outro qualquer (ver
database/models/anonymous_identity.py para a motivação completa).

Correção de segurança (aprovação do CÉREBRO, "Free reseta no logout"): esta identidade NUNCA vira
uma conta autenticada, e uma conta autenticada NUNCA vira esta identidade -- são sempre duas linhas
separadas em `users`, para sempre. Antes desta correção, `promote_anonymous_user` mutava o e-mail
do `User` anônimo para o e-mail confirmado no login (o MESMO user_id passava a ser "a conta"). Isso
deixou de ser seguro quando routes/deps.py::get_generation_user passou a escolher a identidade
efetiva por sessão+entitlement (não mais "sessão sempre vence"): uma identidade anônima que já
tivesse virado conta autenticada, se reaproveitada aqui, faria uma requisição SEM sessão operar
como se fosse essa conta -- exatamente o "cookie Free substituindo a sessão autenticada" que o
CÉREBRO proibiu explicitamente. Também quebrava a continuidade de cota: a identidade Free do
dispositivo deixava de existir depois do primeiro login. Por isso login sempre cria/reaproveita uma
conta autenticada SEPARADA (services/auth.py::_get_or_create_verified_user) e este módulo nunca lê
o e-mail informado no login.

Dado histórico: uma linha de `anonymous_identities` cujo `user_id` já foi promovido por uma versão
anterior (e-mail real, verificado) pode existir no banco. `resolve_anon_identity` nunca devolve um
`User` desses como identidade Free -- ver o comentário no corpo da função.

Este módulo é o ÚNICO que cria um User com e-mail sintético e o único que lê/escreve em
`anonymous_identities`.
"""
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from config import ANON_LAST_SEEN_UPDATE_INTERVAL_SECONDS
from database.models import AnonymousIdentity, User
from database.types import utcnow
from utils.security import generate_anon_device_token, hash_anon_device_token

logger = logging.getLogger("minhoca")

# RFC 2606: domínio reservado para documentação/teste, nunca registrável nem roteável -- garante
# que o e-mail sintético do usuário-placeholder nunca seja entregável a ninguém de verdade.
ANON_EMAIL_DOMAIN = "device.invalid"


def is_anonymous_placeholder_email(email: str) -> bool:
    return isinstance(email, str) and email.endswith(f"@{ANON_EMAIL_DOMAIN}")


def _generate_placeholder_email() -> str:
    # A unicidade de verdade é garantida pela UniqueConstraint("email") do banco (ver o retry por
    # IntegrityError em resolve_anon_identity); isto só precisa ser praticamente único.
    return f"anon-{secrets.token_hex(16)}@{ANON_EMAIL_DOMAIN}"


@dataclass(frozen=True)
class AnonIdentityResult:
    user: User
    # Só vem preenchido quando created=True -- é a ÚNICA vez que o cookie precisa ser setado na
    # resposta (ver routes/deps.py::get_generation_user). Numa reutilização (created=False), o
    # cookie que o navegador já tem continua válido, nada precisa mudar.
    token: str | None
    created: bool


def resolve_anon_identity(
    db: Session, *, device_token: str | None, now: datetime | None = None
) -> AnonIdentityResult:
    """Cookie válido (hash bate com uma linha) -> reaproveita o MESMO user_id. Sem cookie, ou
    cookie que não bate com nada (nunca existiu, ou o navegador o apagou) -> cria um novo
    usuário-dispositivo. Nunca lança para o chamador em uso normal -- sempre devolve um User
    utilizável (ver o único `raise` abaixo, que só ocorre após uma corrida real esgotar o retry)."""
    now = now or utcnow()
    if device_token:
        found = db.execute(
            select(AnonymousIdentity, User)
            .join(User, User.id == AnonymousIdentity.user_id)
            .where(AnonymousIdentity.device_token_hash == hash_anon_device_token(device_token))
        ).first()
        if found is not None:
            identity, user = found
            if not is_anonymous_placeholder_email(user.email):
                # Dado histórico de uma versão anterior deste módulo, que promovia (mutava) o
                # usuário-dispositivo para a conta autenticada no login: esta linha não é mais uma
                # identidade Free válida -- devolvê-la aqui equivaleria a autenticar uma requisição
                # SEM sessão como se fosse essa conta real, só por causa do cookie Free. Tratamos
                # como se o cookie não batesse com nada: cai para criar um usuário-dispositivo novo
                # abaixo. A linha antiga fica órfã e intacta (nenhuma migração/purge automático
                # nesta rodada -- risco documentado no relatório de execução).
                found = None
        if found is not None:
            identity, user = found
            if (
                identity.last_seen_at is None
                or (now - identity.last_seen_at).total_seconds() >= ANON_LAST_SEEN_UPDATE_INTERVAL_SECONDS
            ):
                db.execute(
                    update(AnonymousIdentity).where(AnonymousIdentity.id == identity.id).values(last_seen_at=now)
                )
                db.commit()
            return AnonIdentityResult(user=user, token=None, created=False)

    for attempt in (1, 2):
        try:
            user = User(email=_generate_placeholder_email(), email_verified_at=None)
            db.add(user)
            db.flush()
            token = generate_anon_device_token()
            db.add(
                AnonymousIdentity(
                    user_id=user.id,
                    device_token_hash=hash_anon_device_token(token),
                    created_at=now,
                    last_seen_at=now,
                )
            )
            db.commit()
            logger.info("[ANON] novo usuário-dispositivo criado (user_id=%s)", user.id)
            return AnonIdentityResult(user=user, token=token, created=True)
        except IntegrityError:
            # Corrida improvável (colisão de e-mail sintético ou de hash de token): refaz uma vez
            # com valores novos -- mesmo padrão de retry já usado em services/auth.py::verify_login_code.
            db.rollback()
            if attempt == 2:
                raise
    raise RuntimeError("inalcançável")  # deixa explícito para quem lê; o loop acima sempre retorna ou relança
