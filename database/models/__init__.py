"""Importar este pacote registra todos os modelos no metadata (usado pelo Alembic)."""
from database.models.anonymous_identity import AnonymousIdentity
from database.models.audio_batch import AudioBatch
from database.models.audio_extraction import AudioExtraction
from database.models.auth_session import AuthSession
from database.models.batch import Batch
from database.models.comment import Comment
from database.models.entitlement import Entitlement
from database.models.generation import Generation
from database.models.login_code import LoginCode
from database.models.payment import Payment
from database.models.payment_event import PaymentEvent
from database.models.referral import Referral
from database.models.share_queue_item import ShareQueueItem
from database.models.share_token import ShareToken
from database.models.user import User

__all__ = [
    "AnonymousIdentity",
    "AudioBatch",
    "AudioExtraction",
    "AuthSession",
    "Batch",
    "Comment",
    "Entitlement",
    "Generation",
    "LoginCode",
    "Payment",
    "PaymentEvent",
    "Referral",
    "ShareQueueItem",
    "ShareToken",
    "User",
]
