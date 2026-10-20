"""Isenção de admin na cota de VÍDEOS (Tarefa 3, pedido do CÉREBRO/Igor, 11/10/2026): extensão da
mesma isenção já aplicada à cota de áudio (tests/test_audio_usage.py) para a cota de vídeos/dia
(reserve_generation) e a de OPERAÇÕES de lote/semana (reserve_batch) -- services/usage.py, o motor
de cota/plano do site inteiro. Pedido explícito: o admin (settings.admin_email) nunca é bloqueado
por "estourou a cota" (nem a diária de vídeos, nem a semanal de operações de lote), para poder
baixar sem teto e produzir conteúdo do próprio projeto (ex.: importar uma pasta inteira do
Pinterest). A ÚNICA isenção é o teto numérico: "uma operação por vez" (lock_user_row/idempotência),
`BatchNotAllowedError` (plano sem lote) e o teto de itens POR lote (`Plan.max_batch_size`)
continuam valendo normalmente para o admin -- mesmo desenho e mesmos nomes de teste de
test_audio_usage.py, só que sobre a cota de vídeos (reserve_generation/reserve_batch) em vez da
cota de áudio.

settings.admin_email é a MESMA fonte de verdade já usada em routes/deps.py::require_admin
(moderação de comentários) e em services/audio_usage.py (cota de áudio) -- reaproveitada aqui,
nunca um e-mail hardcoded. use_settings (tests/conftest.py) troca config.settings só para o teste
(monkeypatch), mesmo padrão já usado pelos testes de autenticação/comentários/áudio.
"""
from sqlalchemy import func, select

from database.models import Batch, Generation
from helpers_usage import NOW, consume, give
from services.usage import (
    BatchNotAllowedError,
    BatchQuotaExceededError,
    QuotaExceededError,
    get_allowance,
    get_batch_allowance,
    reserve_batch,
    reserve_generation,
)


# ============================================================================ reserva avulsa (vídeos/dia)
def test_admin_consegue_gerar_a_16a_vez_sem_bloquear(factory, session, use_settings):
    use_settings(admin_email="igoranoruan@gmail.com")
    admin = factory.user(email="igoranoruan@gmail.com")
    give(factory, session, admin, "vip_batch", now=NOW)  # vip_batch: 15 gerações/dia

    consume(session, admin, 15, now=NOW, prefix="admin")  # esgota os 15/dia normalmente

    # a 16a (e a 17a) NUNCA levanta QuotaExceededError para o admin
    reserva_16 = reserve_generation(session, user_id=admin.id, request_id="admin-15", now=NOW)
    assert reserva_16.created is True
    reserva_17 = reserve_generation(session, user_id=admin.id, request_id="admin-16", now=NOW)
    assert reserva_17.created is True

    # a geração do admin é GRAVADA NORMALMENTE no ledger -- "usado" reflete a realidade (17),
    # "remaining" nunca fica negativo (max(0, ...) já existente em Allowance).
    total = session.execute(
        select(func.count()).select_from(Generation).where(Generation.user_id == admin.id)
    ).scalar_one()
    assert total == 17
    saldo = get_allowance(session, admin.id, now=NOW)
    assert saldo.used == 17 and saldo.remaining == 0
    # unlimited=True (11/10/2026): avisa quem LÊ este Allowance (routes/me.py::UsageOut.unlimited)
    # que remaining==0 não deve travar nada no frontend desta conta -- números de used/limit/
    # remaining continuam os REAIS do ledger, só o aviso muda.
    assert saldo.unlimited is True


def test_usuario_comum_continua_bloqueado_na_16a_mesmo_com_admin_email_configurado(
    factory, session, use_settings
):
    """Mesmo teste de um esgotamento normal, mas com ADMIN_EMAIL configurado para OUTRO e-mail --
    confirma que a isenção nunca vaza para quem não é o admin."""
    use_settings(admin_email="igoranoruan@gmail.com")
    usuario = factory.user(email="outra-pessoa@exemplo.com")
    give(factory, session, usuario, "vip_batch", now=NOW)

    consume(session, usuario, 15, now=NOW, prefix="u")

    assert get_allowance(session, usuario.id, now=NOW).unlimited is False
    try:
        reserve_generation(session, user_id=usuario.id, request_id="u-15", now=NOW)
        assert False, "deveria ter levantado QuotaExceededError"
    except QuotaExceededError:
        pass


def test_sem_admin_email_configurado_ninguem_fica_isento(factory, session, use_settings):
    """ADMIN_EMAIL vazio (não configurado) -- mesmo um usuário com ESSE e-mail nunca fica isento
    (a checagem `if not settings.admin_email: return False` é uma guarda explícita em _is_admin;
    este teste documenta a intenção, não é só uma coincidência)."""
    use_settings(admin_email="")
    quase_admin = factory.user(email="igoranoruan@gmail.com")
    give(factory, session, quase_admin, "vip_batch", now=NOW)

    consume(session, quase_admin, 15, now=NOW, prefix="q")

    try:
        reserve_generation(session, user_id=quase_admin.id, request_id="q-15", now=NOW)
        assert False, "deveria ter levantado QuotaExceededError"
    except QuotaExceededError:
        pass


# ============================================================================ lote (vídeos/dia + operações/semana + itens/lote)
def test_admin_consegue_reservar_lote_alem_do_saldo_diario(factory, session, use_settings):
    """A isenção também vale para reserve_batch -- o admin consegue reservar um lote mesmo com o
    saldo DIÁRIO de vídeos já quase esgotado."""
    use_settings(admin_email="igoranoruan@gmail.com")
    admin = factory.user(email="igoranoruan@gmail.com")
    give(factory, session, admin, "vip_batch", now=NOW)  # vip_batch: max_batch_size=15

    consume(session, admin, 12, now=NOW, prefix="admin-pre")  # restam só 3/15
    saldo = get_allowance(session, admin.id, now=NOW)
    assert saldo.remaining == 3

    # pede 5 (além do saldo diário de 3, mas dentro do teto do plano de 15) -- aceito pelo admin
    reserva = reserve_batch(session, user_id=admin.id, request_id="admin-lote-r1", size=5, now=NOW)
    assert reserva.created is True and reserva.item_count == 5


def test_admin_consegue_reservar_lote_alem_do_teto_de_itens_do_plano(factory, session, use_settings):
    """11/10/2026, pedido explícito do Igor (importação de pasta do Pinterest maior que o teto de
    qualquer plano): o teto de itens POR lote (`Plan.max_batch_size`) também passou a ser isento
    pro admin -- antes desta mudança era a única trava numérica que ainda bloqueava o admin."""
    use_settings(admin_email="igoranoruan@gmail.com")
    admin = factory.user(email="igoranoruan@gmail.com")
    give(factory, session, admin, "vip_batch", now=NOW)  # vip_batch: max_batch_size=15

    # pede 40 (muito além do teto do plano, 15) -- NUNCA levanta InvalidBatchSizeError para o admin
    reserva = reserve_batch(session, user_id=admin.id, request_id="admin-lote-gigante", size=40, now=NOW)
    assert reserva.created is True and reserva.item_count == 40

    saldo = get_allowance(session, admin.id, now=NOW)
    assert saldo.used == 40  # gravado normalmente no ledger, sem nenhum número forjado


def test_usuario_comum_continua_bloqueado_pelo_teto_de_itens_do_plano_mesmo_com_admin_email_configurado(
    factory, session, use_settings,
):
    """Mesmo teste acima, mas com ADMIN_EMAIL configurado para OUTRO e-mail -- confirma que a
    isenção do teto de itens por lote nunca vaza para quem não é o admin."""
    use_settings(admin_email="igoranoruan@gmail.com")
    usuario = factory.user(email="outra-pessoa@exemplo.com")
    give(factory, session, usuario, "vip_batch", now=NOW)

    try:
        reserve_batch(session, user_id=usuario.id, request_id="u-lote-gigante", size=16, now=NOW)
        assert False, "deveria ter levantado InvalidBatchSizeError"
    except Exception as exc:
        from services.usage import InvalidBatchSizeError
        assert isinstance(exc, InvalidBatchSizeError)


def test_admin_consegue_reservar_lote_alem_da_cota_semanal_de_operacoes(factory, session, use_settings):
    """Mensal: batch_limit=5 operações/semana -- o admin consegue exceder essa cota (dimensão
    independente da de vídeos), sem levantar BatchQuotaExceededError."""
    use_settings(admin_email="igoranoruan@gmail.com")
    admin = factory.user(email="igoranoruan@gmail.com")
    give(factory, session, admin, "monthly", now=NOW)  # monthly: 10/dia, 5 operações/semana, teto 10/lote

    for i in range(5):  # esgota as 5 operações/semana normalmente
        reserve_batch(session, user_id=admin.id, request_id=f"admin-op-{i}", size=1, now=NOW)

    saldo_ops = get_batch_allowance(session, admin.id, now=NOW)
    assert saldo_ops.remaining == 0

    # a 6a operação NUNCA levanta BatchQuotaExceededError para o admin
    reserva_6 = reserve_batch(session, user_id=admin.id, request_id="admin-op-6", size=1, now=NOW)
    assert reserva_6.created is True

    total_batches = session.execute(
        select(func.count()).select_from(Batch).where(Batch.user_id == admin.id)
    ).scalar_one()
    assert total_batches == 6


def test_usuario_comum_continua_bloqueado_por_operacoes_mesmo_com_admin_email_configurado(
    factory, session, use_settings,
):
    use_settings(admin_email="igoranoruan@gmail.com")
    usuario = factory.user(email="outra-pessoa@exemplo.com")
    give(factory, session, usuario, "monthly", now=NOW)

    for i in range(5):
        reserve_batch(session, user_id=usuario.id, request_id=f"u-op-{i}", size=1, now=NOW)

    try:
        reserve_batch(session, user_id=usuario.id, request_id="u-op-6", size=1, now=NOW)
        assert False, "deveria ter levantado BatchQuotaExceededError"
    except BatchQuotaExceededError:
        pass


def test_admin_continua_sem_lote_no_plano_free(factory, session, use_settings):
    """Free nunca tem lote habilitado (BatchNotAllowedError) -- isso NÃO é uma checagem de cota
    numérica, então a isenção de admin nunca se aplica aqui, igual ao documentado em _is_admin."""
    use_settings(admin_email="igoranoruan@gmail.com")
    admin = factory.user(email="igoranoruan@gmail.com")  # sem give(): fica no Free

    try:
        reserve_batch(session, user_id=admin.id, request_id="admin-free-lote", size=1, now=NOW)
        assert False, "deveria ter levantado BatchNotAllowedError"
    except BatchNotAllowedError:
        pass
