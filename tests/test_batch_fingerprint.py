"""services/batch_fingerprint.py: fingerprint determinístico do conteúdo de um lote (Etapa 9.3,
correção de idempotência real)."""
from services.batch_fingerprint import compute_batch_fingerprint


def test_mesma_entrada_produz_o_mesmo_fingerprint():
    itens = [("https://a", "video-a.mp4"), ("https://b", None)]
    assert compute_batch_fingerprint(itens) == compute_batch_fingerprint(list(itens))


def test_e_um_hex_digest_sha256_de_64_caracteres():
    fingerprint = compute_batch_fingerprint([("https://a", None)])
    assert len(fingerprint) == 64
    assert all(c in "0123456789abcdef" for c in fingerprint)


def test_ordem_diferente_produz_fingerprint_diferente():
    a = compute_batch_fingerprint([("https://a", "x"), ("https://b", None)])
    b = compute_batch_fingerprint([("https://b", None), ("https://a", "x")])
    assert a != b


def test_url_diferente_produz_fingerprint_diferente():
    a = compute_batch_fingerprint([("https://a", None)])
    b = compute_batch_fingerprint([("https://b", None)])
    assert a != b


def test_filename_diferente_produz_fingerprint_diferente():
    a = compute_batch_fingerprint([("https://a", "video-a.mp4")])
    b = compute_batch_fingerprint([("https://a", "outro-nome.mp4")])
    assert a != b


def test_um_item_a_mais_produz_fingerprint_diferente():
    a = compute_batch_fingerprint([("https://a", None)])
    b = compute_batch_fingerprint([("https://a", None), ("https://b", None)])
    assert a != b


def test_filename_none_e_diferente_de_filename_string_vazia_por_construcao():
    """Não é uma garantia central da função (string vazia nunca chega aqui, já vira None antes em
    sanitize_batch_filename), mas prova que a serialização não colapsa os dois."""
    a = compute_batch_fingerprint([("https://a", None)])
    b = compute_batch_fingerprint([("https://a", "")])
    assert a != b