"""Geração do external_reference de um Payment: um token opaco, gerado SOMENTE aqui, nunca
aceito como entrada externa -- mesmo padrão de services/result_storage.py::save() (storage_key)
e services/batch_fingerprint.py (nunca derivado de dado enviado pelo cliente).

uuid4().hex: 32 caracteres hexadecimais em minúsculas, sem nenhuma relação matemática com
user_id/plan_code/preço -- não é adivinhável, nem serve para o cliente inferir algo sobre outro
pagamento. A unicidade de fato é garantida pela UniqueConstraint de
database.models.payment.Payment.external_reference; este módulo só gera o valor, nunca decide o
que fazer em caso de colisão (extremamente improvável com uuid4, e fora do escopo desta etapa).
"""
import uuid


def generate_external_reference() -> str:
    return uuid.uuid4().hex
