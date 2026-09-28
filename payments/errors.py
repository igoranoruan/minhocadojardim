"""Erros da fundação de pagamentos (Etapa 10.1). Nenhum erro de gateway (Mercado Pago) mora
aqui -- este módulo só cobre a criação do Payment interno; a fronteira com o Mercado Pago
(Etapa 10.2+) terá seus próprios erros, num arquivo próprio, quando existir.

UnknownPlanError é REEXPORTADO de services.plans, nunca duplicado aqui: é a MESMA exceção que
services.plans.get_plan() já levanta para um plan_code inexistente, e payments.service.create_payment
nunca a intercepta nem a traduz -- deixa propagar como já é, exatamente como services/entitlements.py
já reexporta services.locks.UserNotFoundError em vez de reimplementar uma exceção equivalente.
"""
from services.plans import UnknownPlanError  # reexportado, nunca duplicado -- ver docstring acima


class PaymentError(Exception):
    """Erro de regra da fundação de pagamentos."""


class PlanNotPurchasableError(PaymentError):
    """O plan_code existe no catálogo, mas não pode ser comprado (ex.: 'free' -- duration_days is None,
    Plan.paid is False)."""


class InvalidPaymentMethodError(PaymentError):
    """method fora de database.models.payment.PAYMENT_METHODS."""


class PaymentNotFoundError(PaymentError):
    """404 genérico e único (Etapa 10.2, mesma filosofia de BatchNotFoundError/
    GenerationDownloadNotFoundError): o Payment não existe, não é deste usuário, ou não está mais
    "pending" -- as três situações recebem exatamente o mesmo erro, para nunca dar a quem pergunta
    uma forma de distinguir uma da outra."""


class PaymentMethodMismatchError(PaymentError):
    """O method que o Payment Brick efetivamente submeteu (Etapa 10.2) não corresponde ao
    Payment.method gravado na criação (Etapa 10.1) -- ex.: Payment criado como "pix" sendo
    submetido como cartão. Erro controlado: a cobrança nunca chega a ser tentada no Mercado
    Pago quando isso acontece (ver payments/gateway.py -- a checagem acontece ANTES de qualquer
    chamada ao client.py)."""


class PaymentGatewayError(PaymentError):
    """Falha ao FALAR com o Mercado Pago (erro de rede, resposta HTTP fora do esperado, exceção
    do SDK) -- nunca uma recusa de pagamento em si (approved/pending/rejected são respostas
    válidas do gateway, não erros; ver payments/gateway.py). Levantada só por payments/client.py."""


__all__ = [
    "UnknownPlanError",
    "PaymentError",
    "PlanNotPurchasableError",
    "InvalidPaymentMethodError",
    "PaymentNotFoundError",
    "PaymentMethodMismatchError",
    "PaymentGatewayError",
]
