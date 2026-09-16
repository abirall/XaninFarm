"""The payment provider interface.

Every gateway - cash, sandbox, bKash, Nagad, SSLCommerz - implements this
same small surface, so ``apps.payments.services`` never branches on which
one is in play.

Three rules hold for all implementations:

* ``verify()`` asks the *provider* what happened. It never reads a status out
  of the browser's query string.
* ``parse_webhook()`` raises ``SignatureError`` unless the payload proves it
  came from the gateway, and always returns a stable ``event_id`` so the
  caller can deduplicate retries.
* No implementation stores, logs or returns raw card data.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal

from apps.payments.constants import PaymentStatus


class PaymentError(Exception):
    """A payment could not be processed. Message is safe for customers."""

    def __init__(self, message: str):
        self.message = str(message)
        super().__init__(self.message)


class ProviderNotConfigured(PaymentError):
    """The gateway is enabled but its credentials are missing."""


class SignatureError(PaymentError):
    """A webhook did not prove it came from the provider."""


@dataclass(frozen=True)
class PaymentIntent:
    """What the customer should do next to pay."""

    payment: object
    redirect_url: str | None = None
    instructions: str = ""

    @property
    def requires_redirect(self) -> bool:
        return bool(self.redirect_url)


@dataclass(frozen=True)
class VerificationResult:
    """The provider's own answer about a payment's fate."""

    status: str = PaymentStatus.PENDING
    provider_reference: str = ""
    amount: Decimal | None = None
    instrument_hint: str = ""
    failure_reason: str = ""
    raw: dict = field(default_factory=dict)

    @property
    def is_paid(self) -> bool:
        return self.status == PaymentStatus.PAID


@dataclass(frozen=True)
class WebhookResult:
    """A verified, deduplicable notification from a provider."""

    event_id: str
    payment_reference: str = ""
    provider_reference: str = ""
    status: str = PaymentStatus.PENDING
    amount: Decimal | None = None
    raw: dict = field(default_factory=dict)


@dataclass(frozen=True)
class RefundResult:
    status: str = PaymentStatus.PENDING
    provider_reference: str = ""
    raw: dict = field(default_factory=dict)


class PaymentProvider(ABC):
    """Base class for every gateway."""

    #: Must match a value in ``apps.payments.constants.PaymentMethod``.
    code: str = ""
    label: str = ""
    #: True when the customer leaves the site to pay.
    redirects_offsite: bool = False
    supports_refund: bool = False
    #: True only when a verified webhook is the last word on a payment.
    #: Real gateways get re-asked through ``verify()`` even after a valid
    #: signature, because a signature proves origin, not outcome.
    webhook_is_authoritative: bool = False
    #: Config keys that must be non-empty for the provider to work.
    required_config: tuple[str, ...] = ()

    def __init__(self, config: dict | None = None):
        self.config = config or {}

    # --- Configuration --------------------------------------------------------
    @property
    def is_configured(self) -> bool:
        return all(self.config.get(key) for key in self.required_config)

    def ensure_configured(self) -> None:
        if not self.is_configured:
            missing = [key for key in self.required_config if not self.config.get(key)]
            raise ProviderNotConfigured(
                f"{self.label} is not configured (missing: {', '.join(missing)})."
            )

    # --- Flow -----------------------------------------------------------------
    @abstractmethod
    def initiate(self, payment, *, request=None) -> PaymentIntent:
        """Start a payment and say where to send the customer."""

    @abstractmethod
    def verify(self, payment, *, payload: dict | None = None) -> VerificationResult:
        """Ask the provider what actually happened. Server-to-server only."""

    def parse_webhook(self, request) -> WebhookResult:
        """Validate and decode a callback. Raise SignatureError if unproven."""
        raise SignatureError(f"{self.label} does not accept webhooks.")

    def refund(self, payment, *, amount: Decimal, reason: str = "") -> RefundResult:
        raise PaymentError(f"{self.label} refunds must be issued manually.")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{type(self).__name__} code={self.code!r} configured={self.is_configured}>"
