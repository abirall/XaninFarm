"""Payment provider implementations.

Adding a gateway means writing one module here and registering it in
``registry.py``. The rest of the application talks only to the
``PaymentProvider`` interface, so nothing outside this package knows which
gateway is in use.
"""

from apps.payments.providers.base import (
    PaymentError,
    PaymentIntent,
    PaymentProvider,
    ProviderNotConfigured,
    RefundResult,
    SignatureError,
    VerificationResult,
    WebhookResult,
)
from apps.payments.providers.registry import available_providers, get_provider

__all__ = [
    "PaymentError",
    "PaymentIntent",
    "PaymentProvider",
    "ProviderNotConfigured",
    "RefundResult",
    "SignatureError",
    "VerificationResult",
    "WebhookResult",
    "available_providers",
    "get_provider",
]
