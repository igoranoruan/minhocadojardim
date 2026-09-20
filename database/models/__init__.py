"""Importar este pacote registra todos os modelos no metadata (usado pelo Alembic)."""
from database.models.batch import Batch
from database.models.entitlement import Entitlement
from database.models.generation import Generation
from database.models.payment import Payment
from database.models.payment_event import PaymentEvent
from database.models.user import User

__all__ = ["Batch", "Entitlement", "Generation", "Payment", "PaymentEvent", "User"]
