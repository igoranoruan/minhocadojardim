"""Identidade anônima (Free sem login -- aprovação do CÉREBRO, Free anônimo).

Cria/reaproveita um `User` "placeholder" (e-mail sintético @device.invalid, nunca verificado) para
um visitante sem sessão, e promove esse mesmo `user_id` para a conta real quando ele faz login com
um e-mail que ainda não existe. Nenhuma outra parte do projeto (services/usage.py,
services/entitlements.py, services/locks.py, download por Generation.user_id) precisa saber que um
usuário é "anônimo" -- para elas, é um user_id como outro qualquer (ver
database/models/anonymous_identity.py para a motivação completa).

Este módulo é o ÚNICO que cria um User com e-mail sintético, o único que lê/escreve em
`anonymous_identities`, e o único que decide a política de "sem merge automático" (aprovada pelo
CÉREBRO): se o e-mail do login já pertence a outro usuário, a conta anônima NUNCA é fundida --
fica intacta e órfã, e o login segue normalmente na conta já existente.
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


def find_anon_user_id_by_token(db: Session, device_token: str | None) -> int | None:
    """Só leitura -- usado por routes/auth.py::verify_code para saber se HÁ uma identidade
    anônima associada ao cookie, sem criar nada (criar uma identidade nova não faz sentido no
    meio de um login) e sem atualizar last_seen_at (não é uma visita de geração)."""
    if not device_token:
        return None
    return db.execute(
        select(AnonymousIdentity.user_id).where(
            AnonymousIdentity.device_token_hash == hash_anon_device_token(device_token)
        )
    ).scalar_one_or_none()


def promote_anonymous_user(db: Session, *, anon_user_id: int | None, email: str, now: datetime) -> User:
    """Chamado por services/auth.py::verify_login_code QUANDO o código foi confirmado com um
    cookie de identidade anônima presente. Duas situações (política aprovada pelo CÉREBRO):

    - E-mail novo (nenhum `User` com este e-mail ainda) -> promove o MESMO `user_id` anônimo: ele
      vira a conta definitiva (email real, email_verified_at=now). O histórico de gerações
      (Generation.user_id) é preservado automaticamente -- nenhuma linha é migrada, porque o
      user_id não muda.
    - E-mail já pertence a outro `User` -> NÃO funde. Login segue normalmente na conta já
      existente; a conta anônima (e seu consumo de Free) fica intacta e órfã -- nunca é apagada
      nem alterada, nunca corrompendo o ownership de nenhuma das duas contas.
    """
    existing = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if existing is not None:
        if existing.email_verified_at is None:
            existing.email_verified_at = now
        return existing

    anon_user = db.get(User, anon_user_id) if anon_user_id is not None else None
    if anon_user is None or not is_anonymous_placeholder_email(anon_user.email):
        # Defensivo, não deveria acontecer em uso normal: anon_user_id só chega aqui já resolvido
        # pelo próprio backend (routes/auth.py via find_anon_user_id_by_token), nunca informado
        # pelo cliente. Se ainda assim vier inválido, comportamento seguro é criar a conta normal.
        user = User(email=email, email_verified_at=now)
        db.add(user)
        db.flush()
        return user

    anon_user.email = email
    anon_user.email_verified_at = now
    db.flush()
    logger.info("[ANON] usuário-dispositivo %s promovido a conta autenticada", anon_user.id)
    return anon_user
