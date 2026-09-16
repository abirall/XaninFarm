"""Cash on delivery.

There is no gateway: the rider collects the money and a staff member records
it. The provider exists so checkout can treat COD like any other method.
"""

from __future__ import annotations

from decimal import Decimal

from apps.payments.constants import PaymentMethod, PaymentStatus
from apps.payments.providers.base import (
    PaymentIntent,
    PaymentProvider,
    VerificationResult,
)


class CashOnDeliveryProvider(PaymentProvider):
    code = PaymentMethod.COD
    label = "Cash on delivery"
    redirects_offsite = False
    supports_refund = False

    @property
    def is_configured(self) -> bool:
        return True

    def initiate(self, payment, *, request=None) -> PaymentIntent:
        return PaymentIntent(
            payment=payment,
            instructions=(
                "Keep the exact amount ready. Our rider will collect it when "
                "your order arrives."
            ),
        )

    def verify(self, payment, *, payload: dict | None = None) -> VerificationResult:
        """COD is only ever settled by the delivery flow, never by polling."""
        return VerificationResult(
            status=payment.status,
            provider_reference=payment.provider_reference,
            amount=Decimal(payment.amount),
        )

    def collect(self, payment) -> VerificationResult:
        """Called when the rider hands the cash in."""
        return VerificationResult(
            status=PaymentStatus.PAID,
            provider_reference=payment.reference,
            amount=Decimal(payment.amount),
            instrument_hint="Cash",
        )
