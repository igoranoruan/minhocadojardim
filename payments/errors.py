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


__all__ = [
    "UnknownPlanError",
    "PaymentError",
    "PlanNotPurchasableError",
    "InvalidPaymentMethodError",
]
