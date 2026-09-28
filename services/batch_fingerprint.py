"""Fingerprint determinístico do CONTEÚDO de um lote (Etapa 9.3, correção de idempotência real).

Detecta "mesmo request_id, mesma quantidade, conteúdo diferente" -- um caso que
services.usage.reserve_batch/_batch_replay (Etapa 9.2) não cobria: só comparava `item_count`.

Determinístico por construção: uma lista de pares [url, filename] (NUNCA um dict com chaves fora
de ordem, nem repr()/hash() do Python, que não são estáveis entre processos) serializada com
json.dumps(..., separators=(",", ":")) -- mesma entrada sempre produz a mesma string de bytes,
em qualquer processo/máquina -- e então SHA-256 (mesmo algoritmo já usado no projeto para
output_sha256, ver processor/service.py). A ORDEM da lista importa (list, não set/dict) -- dois
lotes com os mesmos itens em ordem diferente têm fingerprints diferentes, de propósito (a posição
de cada item é significativa: define qual Generation/qual vídeo cada item vira).

A criação (rota, antes de reserve_batch) e a comparação (reserve_batch/_batch_replay, ao repetir um
request_id) usam EXATAMENTE esta função -- nunca duas implementações."""
import hashlib
import json


def compute_batch_fingerprint(items: list[tuple[str, str | None]]) -> str:
    """`items`: lista de (url, filename_já_sanitizado_ou_None), NA ORDEM submetida. Devolve um
    hex digest SHA-256 (64 caracteres) -- mesmo formato de output_sha256."""
    payload = json.dumps([[url, filename] for url, filename in items], ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()