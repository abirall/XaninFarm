"""Provider registry.

Which gateways exist is decided here; which ones a customer may actually
choose is decided by ``ENABLED_PAYMENT_PROVIDERS`` in the environment, and
whether one *works* is decided by its own credentials.

Keeping those three questions separate means a half-configured gateway
disappears from checkout instead of failing once the customer has committed.
"""

from __future__ import annotations

from django.conf import settings

from apps.payments.constants import PaymentMethod
from apps.payments.providers.base import PaymentError, PaymentProvider
from apps.payments.providers.bkash import BkashProvider
from apps.payments.providers.cod import CashOnDeliveryProvider
from apps.payments.providers.mock import MockProvider
from apps.payments.providers.nagad import NagadProvider
from apps.payments.providers.sslcommerz import SslcommerzProvider

PROVIDER_CLASSES: dict[str, type[PaymentProvider]] = {
    PaymentMethod.COD: CashOnDeliveryProvider,
    PaymentMethod.MOCK: MockProvider,
    PaymentMethod.BKASH: BkashProvider,
    PaymentMethod.NAGAD: NagadProvider,
    PaymentMethod.SSLCOMMERZ: SslcommerzProvider,
}


def enabled_codes() -> list[str]:
    """Codes the operator has switched on, in the order they configured them."""
    configured = getattr(settings, "ENABLED_PAYMENT_PROVIDERS", []) or []
    return [code for code in configured if code in PROVIDER_CLASSES]


def build_provider(code: str) -> PaymentProvider:
    """Instantiate a provider regardless of whether it is enabled.

    Used by webhook handling and by staff tools, which must still be able to
    settle a payment taken through a gateway that has since been switched off.
    """
    try:
        provider_class = PROVIDER_CLASSES[code]
    except KeyError:
        raise PaymentError("That payment method is not available.") from None
    return provider_class(settings.PAYMENT_PROVIDERS.get(code, {}))


def get_provider(code: str) -> PaymentProvider:
    """The provider a customer may pay with right now."""
    if code not in enabled_codes():
        raise PaymentError("That payment method is not available.")
    return build_provider(code)


def available_providers() -> list[PaymentProvider]:
    """Enabled *and* credentialled providers - what checkout should offer."""
    providers = (build_provider(code) for code in enabled_codes())
    return [provider for provider in providers if provider.is_configured]


def is_available(code: str) -> bool:
    return any(provider.code == code for provider in available_providers())
