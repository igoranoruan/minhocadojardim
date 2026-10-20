"""Fila do atalho "Enviar para o KLANGO" (15/10/2026): normalização de URL, extração de link de
texto, validação/dedup/limite/rate-limit de add_to_queue, e send_to_batch respeitando o teto do
plano (Plan.max_batch_size)."""
import pytest

from config import SHARE_QUEUE_MAX_PENDING, SHARE_QUEUE_RATE_LIMIT_PER_HOUR
from database.types import utcnow
from helpers_usage import give
from services.share_queue import (
    _attempts_by_user,
    add_to_queue,
    clear_queue,
    extract_url_from_text,
    list_queue,
    normalize_share_url,
    remove_item,
    send_to_batch,
)


@pytest.fixture(autouse=True)
def _limpa_rate_limit_em_memoria():
    """O rate limit é em memória (mesmo padrão de services/pinterest_board.py) -- precisa ser
    limpo entre testes, senão um teste contamina o contador do outro (mesmo user_id reaproveitado
    entre execuções do SQLite não é o problema aqui; o contador é por id numérico, que RECOMEÇA do
    1 a cada teste via a fixture `factory`, mas limpar explicitamente deixa a intenção clara e
    protege contra qualquer futura mudança na fixture)."""
    _attempts_by_user.clear()
    yield
    _attempts_by_user.clear()


TIKTOK_URL = "https://www.tiktok.com/@alguem/video/123"
INSTAGRAM_URL = "https://www.instagram.com/reel/ABC123/"


# ------------------------------------------------------------------ normalize_share_url
def test_normalize_remove_parametros_de_rastreio_conhecidos():
    bruta = "https://www.instagram.com/reel/ABC123/?igsh=xyz&utm_source=ig&si=1"
    assert normalize_share_url(bruta) == "https://www.instagram.com/reel/ABC123/"


def test_normalize_preserva_parametros_desconhecidos_e_a_ordem():
    bruta = "https://www.tiktok.com/@a/video/1?igsh=xyz&lang=pt&utm_medium=social&ref=home"
    assert normalize_share_url(bruta) == "https://www.tiktok.com/@a/video/1?lang=pt&ref=home"


def test_normalize_sem_query_string_fica_igual():
    assert normalize_share_url(TIKTOK_URL) == TIKTOK_URL


# ------------------------------------------------------------------ extract_url_from_text
def test_extract_url_from_text_acha_a_primeira_url():
    texto = f"olha esse vídeo {INSTAGRAM_URL} muito bom"
    assert extract_url_from_text(texto) == INSTAGRAM_URL


def test_extract_url_from_text_remove_pontuacao_de_fim_de_frase():
    assert extract_url_from_text(f"vê isso: {INSTAGRAM_URL}.") == INSTAGRAM_URL
    assert extract_url_from_text(f"({INSTAGRAM_URL})") == INSTAGRAM_URL


def test_extract_url_from_text_none_quando_nao_ha_url():
    assert extract_url_from_text("não tem link nenhum aqui") is None
    assert extract_url_from_text("") is None


# ------------------------------------------------------------------ add_to_queue
def test_add_to_queue_com_url_valida_adiciona_e_devolve_mensagem_com_contagem(factory, session):
    usuario = factory.user()
    resultado = add_to_queue(session, usuario, url=TIKTOK_URL, text=None)
    assert resultado.added is True
    assert resultado.message == "✅ Adicionado ao KLANGO (1 na fila)"
    assert resultado.pending_count == 1


def test_add_to_queue_extrai_url_de_dentro_de_texto_livre(factory, session):
    usuario = factory.user()
    resultado = add_to_queue(session, usuario, url=None, text=f"compartilhando: {TIKTOK_URL}")
    assert resultado.added is True


def test_add_to_queue_sem_url_nem_texto_com_link_falha(factory, session):
    usuario = factory.user()
    resultado = add_to_queue(session, usuario, url=None, text="sem link nenhum")
    assert resultado.added is False
    assert resultado.message == "❌ Link não encontrado."


def test_add_to_queue_url_de_plataforma_nao_suportada_falha(factory, session):
    usuario = factory.user()
    resultado = add_to_queue(session, usuario, url="https://exemplo-qualquer.com/video", text=None)
    assert resultado.added is False
    assert resultado.message == "❌ Link não suportado."


def test_add_to_queue_deduplica_url_pendente_do_mesmo_usuario(factory, session):
    usuario = factory.user()
    add_to_queue(session, usuario, url=TIKTOK_URL, text=None)
    resultado = add_to_queue(session, usuario, url=TIKTOK_URL, text=None)
    assert resultado.added is False
    assert resultado.message == "⚠️ Link já estava na fila"
    assert len(list_queue(session, usuario)) == 1


def test_add_to_queue_mesma_url_em_usuarios_diferentes_nao_e_duplicata(factory, session):
    usuario_a = factory.user()
    usuario_b = factory.user()
    assert add_to_queue(session, usuario_a, url=TIKTOK_URL, text=None).added is True
    assert add_to_queue(session, usuario_b, url=TIKTOK_URL, text=None).added is True


def test_add_to_queue_remove_parametro_de_rastreio_antes_de_deduplicar(factory, session):
    usuario = factory.user()
    add_to_queue(session, usuario, url=TIKTOK_URL + "?igsh=abc", text=None)
    resultado = add_to_queue(session, usuario, url=TIKTOK_URL + "?igsh=outrovalor", text=None)
    assert resultado.added is False
    assert resultado.message == "⚠️ Link já estava na fila"


def test_add_to_queue_recusa_quando_fila_esta_cheia(factory, session):
    usuario = factory.user()
    for i in range(SHARE_QUEUE_MAX_PENDING):
        resultado = add_to_queue(session, usuario, url=f"https://www.tiktok.com/@a/video/{i}", text=None)
        assert resultado.added is True

    cheio = add_to_queue(session, usuario, url="https://www.tiktok.com/@a/video/extra", text=None)
    assert cheio.added is False
    assert "Fila cheia" in cheio.message


def test_add_to_queue_aplica_rate_limit_por_hora(factory, session):
    usuario = factory.user()
    for i in range(SHARE_QUEUE_RATE_LIMIT_PER_HOUR):
        add_to_queue(session, usuario, url=f"https://www.tiktok.com/@a/video/rl{i}", text=None)

    excedente = add_to_queue(session, usuario, url="https://www.tiktok.com/@a/video/excedente", text=None)
    assert excedente.added is False
    assert "Muitos links" in excedente.message


def test_rate_limit_e_por_usuario_nao_global(factory, session):
    usuario_a = factory.user()
    usuario_b = factory.user()
    for i in range(SHARE_QUEUE_RATE_LIMIT_PER_HOUR):
        add_to_queue(session, usuario_a, url=f"https://www.tiktok.com/@a/video/rl{i}", text=None)

    # usuario_a já estourou o limite; usuario_b continua livre
    assert add_to_queue(session, usuario_b, url=TIKTOK_URL, text=None).added is True


# ------------------------------------------------------------------ list/remove/clear
def test_list_queue_devolve_so_pendentes_do_usuario_em_ordem_de_chegada(factory, session):
    usuario = factory.user()
    add_to_queue(session, usuario, url="https://www.tiktok.com/@a/video/1", text=None)
    add_to_queue(session, usuario, url="https://www.tiktok.com/@a/video/2", text=None)

    itens = list_queue(session, usuario)
    assert [item.url for item in itens] == [
        "https://www.tiktok.com/@a/video/1", "https://www.tiktok.com/@a/video/2",
    ]


def test_remove_item_remove_so_o_item_do_dono_e_e_idempotente(factory, session):
    usuario_a = factory.user()
    usuario_b = factory.user()
    add_to_queue(session, usuario_a, url=TIKTOK_URL, text=None)
    item = list_queue(session, usuario_a)[0]

    assert remove_item(session, usuario_b, item.id) is False  # não é dono
    assert len(list_queue(session, usuario_a)) == 1

    assert remove_item(session, usuario_a, item.id) is True
    assert list_queue(session, usuario_a) == []
    assert remove_item(session, usuario_a, item.id) is False  # já não está pendente


def test_clear_queue_remove_todos_os_pendentes_e_devolve_a_contagem(factory, session):
    usuario = factory.user()
    add_to_queue(session, usuario, url="https://www.tiktok.com/@a/video/1", text=None)
    add_to_queue(session, usuario, url="https://www.tiktok.com/@a/video/2", text=None)

    assert clear_queue(session, usuario) == 2
    assert list_queue(session, usuario) == []


# ------------------------------------------------------------------ send_to_batch
def test_send_to_batch_sem_plano_pago_nao_envia_nada(factory, session):
    usuario = factory.user()
    add_to_queue(session, usuario, url=TIKTOK_URL, text=None)

    resultado = send_to_batch(session, usuario)
    assert resultado.urls == []
    assert resultado.sent_count == 0
    assert resultado.max_batch_size == 0
    assert len(list_queue(session, usuario)) == 1  # nada foi consumido


def test_send_to_batch_respeita_o_teto_do_plano_e_mantem_o_excedente_na_fila(factory, session):
    usuario = factory.user()
    give(factory, session, usuario, "vip_batch", now=utcnow())  # max_batch_size = 15
    for i in range(20):
        add_to_queue(session, usuario, url=f"https://www.tiktok.com/@a/video/{i}", text=None)

    resultado = send_to_batch(session, usuario)
    assert resultado.sent_count == 15
    assert resultado.max_batch_size == 15
    assert len(resultado.urls) == 15
    assert resultado.remaining_pending == 5
    assert len(list_queue(session, usuario)) == 5  # só o excedente continua pendente


def test_send_to_batch_pega_os_itens_mais_antigos_primeiro(factory, session):
    usuario = factory.user()
    give(factory, session, usuario, "weekly", now=utcnow())  # max_batch_size = 10 (20/10/2026)
    for i in range(11):
        add_to_queue(session, usuario, url=f"https://www.tiktok.com/@a/video/{i}", text=None)

    resultado = send_to_batch(session, usuario)
    assert resultado.urls == [f"https://www.tiktok.com/@a/video/{i}" for i in range(10)]
    restantes = [item.url for item in list_queue(session, usuario)]
    assert restantes == [
        "https://www.tiktok.com/@a/video/10",
    ]


def test_send_to_batch_sem_itens_pendentes_devolve_vazio(factory, session):
    usuario = factory.user()
    give(factory, session, usuario, "vip_batch", now=utcnow())
    resultado = send_to_batch(session, usuario)
    assert resultado.urls == []
    assert resultado.sent_count == 0
    assert resultado.max_batch_size == 15


# ------------------------------------------------------------------ send_to_batch(max_items=...)
# 17/10/2026 (aprovação do CÉREBRO, ajuste #2 da demanda de extrair áudio pela fila): `max_items`
# existe especificamente para o botão "Extrair áudios" nunca separar (e marcar 'consumed') mais
# itens do que o saldo diário de áudio aceita -- reserve_audio_batch é tudo-ou-nada, então sem isso
# o excedente seria perdido da fila mesmo sem nenhum áudio ter sido extraído.
def test_send_to_batch_com_max_items_menor_que_o_teto_do_plano_respeita_o_menor(factory, session):
    usuario = factory.user()
    give(factory, session, usuario, "vip_batch", now=utcnow())  # max_batch_size = 15
    for i in range(10):
        add_to_queue(session, usuario, url=f"https://www.tiktok.com/@a/video/{i}", text=None)

    resultado = send_to_batch(session, usuario, max_items=3)  # saldo diário de áudio = 3
    assert resultado.sent_count == 3
    assert resultado.max_batch_size == 15  # o teto REPORTADO continua sendo o do plano
    assert resultado.remaining_pending == 7
    assert len(list_queue(session, usuario)) == 7  # o excedente NUNCA é perdido -- continua pendente


def test_send_to_batch_com_max_items_maior_que_o_teto_do_plano_nao_libera_mais_que_o_plano(factory, session):
    usuario = factory.user()
    give(factory, session, usuario, "weekly", now=utcnow())  # max_batch_size = 10 (20/10/2026)
    for i in range(12):
        add_to_queue(session, usuario, url=f"https://www.tiktok.com/@a/video/{i}", text=None)

    resultado = send_to_batch(session, usuario, max_items=100)  # saldo de áudio bem maior que o plano
    assert resultado.sent_count == 10  # ainda trava no teto do plano, max_items nunca AUMENTA o limite
    assert len(list_queue(session, usuario)) == 2


def test_send_to_batch_com_max_items_zero_nao_separa_nada_e_mantem_tudo_pendente(factory, session):
    usuario = factory.user()
    give(factory, session, usuario, "vip_batch", now=utcnow())
    add_to_queue(session, usuario, url=TIKTOK_URL, text=None)

    resultado = send_to_batch(session, usuario, max_items=0)  # saldo diário de áudio zerado por hoje
    assert resultado.urls == []
    assert resultado.sent_count == 0
    assert resultado.max_batch_size == 15  # continua reportando o teto do plano, não 0
    assert len(list_queue(session, usuario)) == 1  # nada foi consumido


def test_send_to_batch_sem_max_items_preserva_o_comportamento_de_sempre(factory, session):
    """Regressão: o botão de VÍDEO nunca passa max_items -- precisa continuar idêntico a antes
    desta mudança."""
    usuario = factory.user()
    give(factory, session, usuario, "vip_batch", now=utcnow())
    for i in range(20):
        add_to_queue(session, usuario, url=f"https://www.tiktok.com/@a/video/{i}", text=None)

    resultado = send_to_batch(session, usuario)
    assert resultado.sent_count == 15
    assert resultado.remaining_pending == 5
