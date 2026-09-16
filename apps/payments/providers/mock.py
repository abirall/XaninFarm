"""Sandbox gateway.

A fully working local provider used when no real credentials are available.
It exercises the same code paths as a live gateway - offsite redirect,
server-side verification on return, and an HMAC-signed webhook - so the
integration is genuinely tested rather than stubbed out.

It is refused outright unless ``PAYMENT_SANDBOX`` is on, so it can never
collect imaginary money in production.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from decimal import Decimal

from django.conf import settings
from django.urls import reverse

from apps.core.utils import money
from apps.payments.constants import PaymentMethod, PaymentStatus
from apps.payments.providers.base import (
    PaymentError,
    PaymentIntent,
    PaymentProvider,
    RefundResult,
    SignatureError,
    VerificationResult,
    WebhookResult,
)

SIGNATURE_HEADER = "HTTP_X_MOCK_SIGNATURE"


class MockProvider(PaymentProvider):
    code = PaymentMethod.MOCK
    label = "Sandbox checkout"
    redirects_offsite = True
    supports_refund = True
    # There is no second endpoint to ask: the signed callback is the sandbox's
    # own word, and the signature already proves it came from us.
    webhook_is_authoritative = True
    required_config = ("secret",)

    def _guard_sandbox(self) -> None:
        if not getattr(settings, "PAYMENT_SANDBOX", False):
            raise PaymentError("The sandbox gateway is disabled on this environment.")

    def initiate(self, payment, *, request=None) -> PaymentIntent:
        self._guard_sandbox()
        self.ensure_configured()
        return PaymentIntent(
            payment=payment,
            redirect_url=reverse(
                "payments:mock_gateway", kwargs={"reference": payment.reference}
            ),
            instructions="You are being sent to the sandbox gateway - no real money moves.",
        )

    def verify(self, payment, *, payload: dict | None = None) -> VerificationResult:
        """Read the outcome the sandbox recorded, exactly as a real
        server-to-server verification call would.

        The browser's query string is deliberately ignored.
        """
        self._guard_sandbox()
        recorded = (payment.raw_response or {}).get("sandbox", {})
        outcome = recorded.get("outcome")

        if outcome == "paid":
            return VerificationResult(
                status=PaymentStatus.PAID,
                provider_reference=recorded.get("transaction_id", payment.reference),
                amount=money(recorded.get("amount", payment.amount)),
                instrument_hint="Sandbox wallet",
                raw=recorded,
            )
        if outcome == "failed":
            return VerificationResult(
                status=PaymentStatus.FAILED,
                failure_reason=recorded.get("reason", "Declined by the sandbox gateway."),
                raw=recorded,
            )
        if outcome == "cancelled":
            return VerificationResult(
                status=PaymentStatus.CANCELLED,
                failure_reason="Payment cancelled.",
                raw=recorded,
            )
        return VerificationResult(status=PaymentStatus.PENDING, raw=recorded)

    # --- Webhook --------------------------------------------------------------
    def sign(self, body: bytes) -> str:
        secret = str(self.config.get("secret", "")).encode()
        return "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()

    def parse_webhook(self, request) -> WebhookResult:
        self._guard_sandbox()
        self.ensure_configured()

        provided = request.META.get(SIGNATURE_HEADER, "")
        if not provided or not hmac.compare_digest(provided, self.sign(request.body)):
            raise SignatureError("Sandbox webhook signature did not match.")

        try:
            payload = json.loads(request.body.decode() or "{}")
        except (ValueError, UnicodeDecodeError) as exc:
            raise SignatureError("Sandbox webhook body was not valid JSON.") from exc

        event_id = str(payload.get("event_id") or "").strip()
        if not event_id:
            raise SignatureError("Sandbox webhook is missing an event id.")

        status_map = {
            "paid": PaymentStatus.PAID,
            "failed": PaymentStatus.FAILED,
            "cancelled": PaymentStatus.CANCELLED,
        }
        amount = payload.get("amount")
        return WebhookResult(
            event_id=event_id,
            payment_reference=str(payload.get("reference") or ""),
            provider_reference=str(payload.get("transaction_id") or ""),
            status=status_map.get(str(payload.get("status")), PaymentStatus.PENDING),
            amount=money(amount) if amount is not None else None,
            raw=payload,
        )

    def refund(self, payment, *, amount: Decimal, reason: str = "") -> RefundResult:
        self._guard_sandbox()
        return RefundResult(
            status=PaymentStatus.REFUNDED,
            provider_reference=f"SBXRF-{payment.reference}",
            raw={"refunded": str(money(amount)), "reason": reason},
        )
