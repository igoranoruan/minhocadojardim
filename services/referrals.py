"""Programa de indicação (Etapa 8 da revisão de UX, aprovação do CÉREBRO, 03/10/2026): quem indica
ganha dias extras de plano quando a pessoa indicada faz o PRIMEIRO pagamento aprovado (nunca no
cadastro/login, que é grátis e fácil de simular com contas falsas).

Não está em BUSINESS (services/plans.py, services/entitlement_chain.py, services/entitlements.py,
services/locks.py, services/usage.py) -- não decide cota nem preço, só orquestra três pontas:

1. Código de indicação por usuário (get_or_create_referral_code) -- gerado SOB DEMANDA.
2. Vínculo indicador -> indicado (claim_referral) -- gravado uma ÚNICA VEZ, pelo PRÓPRIO indicado,
   logo após o login (nunca por quem indica, nunca inferido de cookie/IP/payload de pagamento).
3. A recompensa em si (process_entitlement_granted) -- chamada por payments/gateway.py toda vez
   que um entitlement é concedido (grant_entitlement), nunca direto de uma rota.

Toda mutação de ENTITLEMENT (dias extras) passa por services.entitlements.extend_entitlement --
este módulo nunca toca Entitlement.expires_at diretamente, para herdar as mesmas garantias de
sobreposição/realinhamento que grant_entitlement já tem. Nunca lê/cria Payment, nunca conhece
preço -- a ponte com pagamento é só o `user_id` e o `entitlement` que payments/gateway.py repassa
depois de já ter chamado grant_entitlement com sucesso.
"""
import logging
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from config import (
    REFERRAL_CODE_LENGTH,
    REFERRAL_MILESTONE_EVERY,
    REFERRAL_MILESTONE_PLAN_CODE,
    REFERRAL_MILESTONE_REWARD_DAYS,
    REFERRAL_REWARD_DAYS,
)
from database.models import Entitlement, Referral, User
from services.entitlements import (
    EntitlementError,
    extend_entitlement,
    get_current_entitlement,
    grant_referral_milestone,
)
from utils.security import generate_referral_code
from utils.time_sp import resolve_now

logger = logging.getLogger("minhoca")

_MAX_CODE_GENERATION_ATTEMPTS = 10


class ReferralError(Exception):
    """Erro de regra do programa de indicação."""


class InvalidReferralCodeError(ReferralError):
    pass


class SelfReferralError(ReferralError):
    pass


class AlreadyClaimedError(ReferralError):
    """Este usuário já tem um indicador registrado -- o vínculo é gravado uma única vez na vida."""


def get_last_applied_reward(db: Session, referrer_id: int) -> Referral | None:
    """Última recompensa de indicação já APLICADA (status='applied') a este indicador, ou None se
    nunca recebeu nenhuma -- usada só pela rota GET /api/me/referral (routes/me.py), pra o
    frontend conseguir mostrar um aviso de "indicação confirmada" sem inventar dado nenhum: tudo
    que mostra (quantos dias, se foi um marco com upgrade de plano, quando aconteceu) vem direto
    desta linha. Só leitura -- nenhuma mutação."""
    return db.execute(
        select(Referral)
        .where(Referral.referrer_user_id == referrer_id, Referral.status == "applied")
        .order_by(Referral.applied_at.desc(), Referral.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def get_or_create_referral_code(db: Session, user_id: int) -> str:
    """Devolve o código de indicação do usuário, gerando (e persistindo) um na primeira chamada.
    Retry em caso de colisão -- extremamente improvável com o alfabeto/tamanho de
    utils.security.generate_referral_code (32^8 combinações), mas nunca assumido impossível: a
    coluna é UNIQUE no banco, que é quem decide de verdade."""
    user = db.get(User, user_id)
    if user is None:
        raise LookupError(f"Usuário {user_id} não encontrado.")
    if user.referral_code:
        return user.referral_code

    for _ in range(_MAX_CODE_GENERATION_ATTEMPTS):
        candidato = generate_referral_code(REFERRAL_CODE_LENGTH)
        user.referral_code = candidato
        try:
            db.commit()
            logger.info("[REFERRAL] código gerado user_id=%s", user_id)
            return candidato
        except IntegrityError:
            db.rollback()
            user = db.get(User, user_id)  # relido após o rollback -- mesmo registro, estado limpo
            if user.referral_code:  # corrida: outra requisição concorrente já gerou um pra ele
                return user.referral_code
    raise ReferralError("Não foi possível gerar um código de indicação único.")


def claim_referral(db: Session, *, user_id: int, code: str) -> int:
    """O PRÓPRIO usuário autenticado reivindica o código de outra pessoa, logo após o login
    (routes/me.py). Grava users.referred_by_user_id UMA ÚNICA VEZ -- nunca sobrescreve um vínculo
    já existente (AlreadyClaimedError, mesmo se o código informado desta vez for diferente).

    Devolve o user_id do indicador. Levanta InvalidReferralCodeError (código vazio ou que não
    pertence a ninguém -- mesma mensagem genérica para os dois casos, para nunca virar uma forma
    de sondar quais códigos existem) e SelfReferralError (o código pertence ao próprio usuário)."""
    user = db.get(User, user_id)
    if user is None:
        raise LookupError(f"Usuário {user_id} não encontrado.")
    if user.referred_by_user_id is not None:
        raise AlreadyClaimedError(f"Usuário {user_id} já tem um indicador registrado.")

    code_normalizado = (code or "").strip().upper()
    referrer = (
        db.execute(select(User).where(User.referral_code == code_normalizado)).scalar_one_or_none()
        if code_normalizado else None
    )
    if referrer is None:
        raise InvalidReferralCodeError(f"Código de indicação inválido: {code!r}")
    if referrer.id == user_id:
        raise SelfReferralError("Não é possível usar o próprio código de indicação.")

    user.referred_by_user_id = referrer.id
    db.commit()
    logger.info("[REFERRAL] vínculo criado: user_id=%s indicado por referrer_id=%s", user_id, referrer.id)
    return referrer.id


def _count_referrals_for_referrer(db: Session, referrer_id: int) -> int:
    """Quantas indicações este indicador já converteu NA VIDA (aplicadas OU pending_plan) -- cada
    linha é uma pessoa indicada que já fez o primeiro pagamento, não importa se o bônus já foi
    aplicado ou ainda está esperando o indicador ter um plano vigente. É essa contagem (+1, pela
    indicação que está sendo processada agora) que decide se esta é a indicação que completa um
    ciclo de REFERRAL_MILESTONE_EVERY (marco de indicações, aprovação do CÉREBRO, 04/10/2026)."""
    return db.execute(
        select(func.count(Referral.id)).where(Referral.referrer_user_id == referrer_id)
    ).scalar_one()


def _reverse_days_for_milestone(db: Session, *, referrer_id: int, entitlement_id: int) -> int | None:
    """Soma os `reward_days` das (REFERRAL_MILESTONE_EVERY - 1) indicações ANTERIORES deste
    indicador -- só quando é seguro: todas já 'applied' e todas aplicadas a este MESMO
    `entitlement_id` (nenhuma ficou pending_plan nem foi aplicada a um entitlement diferente, já
    expirado/trocado no meio do ciclo). É a única situação em que desfazer dias já concedidos é
    seguro, porque esses dias ainda estão todos dentro do mesmo acesso vigente, intactos.
    Qualquer outra situação devolve None -- o chamador decide o que fazer, esta função nunca
    adivinha (mesma filosofia de EntitlementInconsistencyError: nunca escolher um resultado
    silenciosamente quando o histórico não está limpo)."""
    anteriores = db.execute(
        select(Referral)
        .where(Referral.referrer_user_id == referrer_id)
        .order_by(Referral.id.desc())
        .limit(REFERRAL_MILESTONE_EVERY - 1)
    ).scalars().all()
    if len(anteriores) != REFERRAL_MILESTONE_EVERY - 1:
        return None
    if any(r.status != "applied" or r.applied_entitlement_id != entitlement_id for r in anteriores):
        return None
    return sum(r.reward_days for r in anteriores)


def _apply_reward_or_queue(db: Session, *, referrer_id: int, referred_id: int, now: datetime) -> Referral:
    """Cria a linha de recompensa (Referral) para `referred_id` -- aplica IMEDIATAMENTE no
    entitlement vigente do indicador, se ele tiver um agora, ou deixa `pending_plan` (aplicado
    depois, na próxima vez que o indicador receber QUALQUER entitlement -- ver
    _apply_pending_rewards_for_referrer, abaixo).

    Marco de indicações (aprovação do CÉREBRO, 04/10/2026): quando esta é a indicação que completa
    um ciclo de REFERRAL_MILESTONE_EVERY (3ª, 6ª, 9ª...), a recompensa desta linha já nasce como
    REFERRAL_MILESTONE_REWARD_DAYS dias de REFERRAL_MILESTONE_PLAN_CODE (upgrade automático) em vez
    de REFERRAL_REWARD_DAYS -- e, se o indicador já tiver um plano vigente agora E as outras duas
    indicações deste ciclo já tiverem sido aplicadas a ESTE MESMO entitlement, os dias delas são
    desfeitos na mesma operação (o total do ciclo vira só o bônus do marco, não a soma dos dois).
    Se o histórico do ciclo não estiver limpo (ex.: indicador ficou Free no meio do ciclo), o marco
    ainda é aplicado, só que por cima -- sem tentar desfazer dias de um jeito arriscado -- e um
    aviso fica no log para revisão manual."""
    ordinal = _count_referrals_for_referrer(db, referrer_id) + 1
    is_milestone = ordinal % REFERRAL_MILESTONE_EVERY == 0
    reward_days = REFERRAL_MILESTONE_REWARD_DAYS if is_milestone else REFERRAL_REWARD_DAYS

    current = get_current_entitlement(db, referrer_id, now)
    if current is None:
        referral = Referral(
            referrer_user_id=referrer_id, referred_user_id=referred_id,
            reward_days=reward_days, status="pending_plan",
        )
        db.add(referral)
        db.commit()
        logger.info(
            "[REFERRAL] recompensa pendente (indicador sem plano vigente agora)%s: referrer_id=%s referred_id=%s",
            " [marco]" if is_milestone else "", referrer_id, referred_id,
        )
        return referral

    if is_milestone:
        reverse_days = _reverse_days_for_milestone(db, referrer_id=referrer_id, entitlement_id=current.id)
        if reverse_days is None:
            logger.warning(
                "[REFERRAL] marco de indicação sem histórico limpo pra reverter neste ciclo -- "
                "aplicando o bônus por cima, sem desfazer dias anteriores: referrer_id=%s "
                "referred_id=%s entitlement_id=%s",
                referrer_id, referred_id, current.id,
            )
            reverse_days = 0
        grant_referral_milestone(
            db, user_id=referrer_id, entitlement_id=current.id,
            new_plan_code=REFERRAL_MILESTONE_PLAN_CODE, reverse_days=reverse_days,
            reward_days=reward_days, now=now,
        )
    else:
        extend_entitlement(db, user_id=referrer_id, entitlement_id=current.id, extra_days=reward_days, now=now)

    referral = Referral(
        referrer_user_id=referrer_id, referred_user_id=referred_id,
        reward_days=reward_days, status="applied",
        applied_entitlement_id=current.id, applied_at=now,
    )
    db.add(referral)
    db.commit()
    logger.info(
        "[REFERRAL] recompensa aplicada na hora%s: referrer_id=%s referred_id=%s entitlement_id=%s dias=%s",
        " [marco]" if is_milestone else "", referrer_id, referred_id, current.id, reward_days,
    )
    return referral


def _apply_pending_rewards_for_referrer(db: Session, *, referrer_id: int, entitlement: Entitlement, now: datetime) -> None:
    """Chamada toda vez que `referrer_id` recebe QUALQUER entitlement novo (não só quando ele
    mesmo acabou de ser indicado por alguém) -- aplica no entitlement recém-concedido qualquer
    recompensa que estava esperando um plano vigente existir (ramo pending_plan de
    _apply_reward_or_queue). Pode aplicar MAIS DE UMA, se o indicador ficou Free por um tempo e
    acumulou indicações pendentes nesse período.

    Uma pendência de MARCO (reward_days == REFERRAL_MILESTONE_REWARD_DAYS -- já decidido no momento
    em que a linha foi criada, em _apply_reward_or_queue) aplica com upgrade de plano
    (grant_referral_milestone), sempre com reverse_days=0: como ela estava pending_plan, nenhum dia
    das outras indicações do mesmo ciclo chegou a ser somado a entitlement algum ainda -- não há o
    que desfazer. Processadas em ordem de criação (ORDER BY id), igual a sempre."""
    pendentes = db.execute(
        select(Referral)
        .where(Referral.referrer_user_id == referrer_id, Referral.status == "pending_plan")
        .order_by(Referral.id)
    ).scalars().all()
    for referral in pendentes:
        if referral.reward_days == REFERRAL_MILESTONE_REWARD_DAYS:
            grant_referral_milestone(
                db, user_id=referrer_id, entitlement_id=entitlement.id,
                new_plan_code=REFERRAL_MILESTONE_PLAN_CODE, reverse_days=0,
                reward_days=referral.reward_days, now=now,
            )
        else:
            extend_entitlement(
                db, user_id=referrer_id, entitlement_id=entitlement.id, extra_days=referral.reward_days, now=now,
            )
        referral.status = "applied"
        referral.applied_entitlement_id = entitlement.id
        referral.applied_at = now
        db.commit()
        logger.info(
            "[REFERRAL] recompensa pendente aplicada: referral_id=%s referrer_id=%s entitlement_id=%s",
            referral.id, referrer_id, entitlement.id,
        )


def process_entitlement_granted(
    db: Session, *, user_id: int, entitlement: Entitlement, now: datetime | None = None
) -> None:
    """ÚNICO ponto de entrada deste módulo para payments/gateway.py -- chamado sempre logo depois
    de um grant_entitlement bem-sucedido (qualquer plano, qualquer usuário). Nunca lê Payment,
    nunca decide preço/plano -- só orquestra as duas pontas do programa de indicação:

    1. Se `user_id` (quem acabou de pagar) foi indicado por alguém E ainda não gerou nenhuma
       recompensa (Referral.referred_user_id é UNIQUE -- a ausência de linha JÁ É a checagem de
       "este é o primeiro pagamento aprovado dele"), cria a recompensa para o indicador agora.
    2. Se `user_id` (possivelmente o indicador de outras pessoas) tinha recompensas pendentes de
       quando ele mesmo estava sem plano vigente, aplica todas no entitlement que ele acabou de
       receber.

    NUNCA levanta -- qualquer erro aqui é logado e engolido: o programa de indicação é um bônus;
    não pode derrubar a confirmação de um pagamento de verdade que já foi concedida com sucesso
    por grant_entitlement ANTES desta função ser chamada (ver payments/gateway.py). Cada uma das
    duas pontas tem seu próprio try/except -- uma falhar não impede a outra de ser tentada."""
    now = resolve_now(now)

    try:
        user = db.get(User, user_id)
        if user is not None and user.referred_by_user_id is not None:
            ja_recompensado = db.execute(
                select(Referral.id).where(Referral.referred_user_id == user_id)
            ).scalar_one_or_none()
            if ja_recompensado is None:
                _apply_reward_or_queue(db, referrer_id=user.referred_by_user_id, referred_id=user_id, now=now)
    except (EntitlementError, ReferralError, IntegrityError) as exc:
        db.rollback()
        logger.error("[REFERRAL] falha ao processar recompensa como indicado: user_id=%s erro=%s", user_id, exc)

    try:
        _apply_pending_rewards_for_referrer(db, referrer_id=user_id, entitlement=entitlement, now=now)
    except (EntitlementError, ReferralError, IntegrityError) as exc:
        db.rollback()
        logger.error("[REFERRAL] falha ao aplicar recompensas pendentes: referrer_id=%s erro=%s", user_id, exc)
