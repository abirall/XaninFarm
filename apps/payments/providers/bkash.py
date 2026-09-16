"""bKash Tokenized Checkout.

Flow (per bKash's merchant documentation):

1. ``POST /tokenized/checkout/token/grant``   -> id_token
2. ``POST /tokenized/checkout/create``        -> paymentID + bkashURL
3. customer authorises on bkashURL and returns to us
4. ``POST /tokenized/checkout/execute``       -> trxID, ``transactionStatus``
5. ``POST /tokenized/checkout/payment/status``-> authoritative state (used by
   ``verify`` so a customer who closes the tab mid-flow is still settled)

Step 4/5 are server-to-server. The browser's ``?status=`` is never trusted.
"""

from __future__ import annotations

import logging
from decimal import Decimal

import requests
from django.core.cache import cache
from django.urls import reverse

from apps.core.utils import money
from apps.payments.constants import PaymentMethod, PaymentStatus
from apps.payments.providers.base import (
    PaymentError,
    PaymentIntent,
    PaymentProvider,
    RefundResult,
    VerificationResult,
)

logger = logging.getLogger(__name__)

TOKEN_CACHE_KEY = "payments:bkash:id_token"
TIMEOUT = 20


class BkashProvider(PaymentProvider):
    code = PaymentMethod.BKASH
    label = "bKash"
    redirects_offsite = True
    supports_refund = True
    required_config = ("base_url", "app_key", "app_secret", "username", "password")

    # --- Plumbing -------------------------------------------------------------
    def _url(self, path: str) -> str:
        return f"{str(self.config['base_url']).rstrip('/')}{path}"

    def _grant_token(self) -> str:
        cached = cache.get(TOKEN_CACHE_KEY)
        if cached:
            return cached

        response = requests.post(
            self._url("/tokenized/checkout/token/grant"),
            json={
                "app_key": self.config["app_key"],
                "app_secret": self.config["app_secret"],
            },
            headers={
                "username": self.config["username"],
                "password": self.config["password"],
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=TIMEOUT,
        )
        data = self._json(response)
        token = data.get("id_token")
        if not token:
            raise PaymentError("bKash did not issue a token. Please try another method.")
        # bKash tokens last an hour; refresh a little early.
        cache.set(TOKEN_CACHE_KEY, token, int(data.get("expires_in", 3600)) - 120)
        return token

    def _headers(self) -> dict:
        return {
            "Authorization": self._grant_token(),
            "X-APP-Key": self.config["app_key"],
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    @staticmethod
    def _json(response) -> dict:
        try:
            data = response.json()
        except ValueError as exc:
            logger.error("bKash returned non-JSON (%s)", response.status_code)
            raise PaymentError("bKash is not responding correctly right now.") from exc
        if response.status_code >= 400:
            logger.error("bKash error %s: %s", response.status_code, data.get("statusMessage"))
        return data

    # --- Flow -----------------------------------------------------------------
    def initiate(self, payment, *, request=None) -> PaymentIntent:
        self.ensure_configured()

        callback = (
            request.build_absolute_uri(
                reverse("payments:return", kwargs={"number": payment.order.number})
            )
            if request is not None
            else ""
        )
        data = self._json(
            requests.post(
                self._url("/tokenized/checkout/create"),
                json={
                    "mode": "0011",  # checkout with callback
                    "payerReference": payment.order.contact_phone,
                    "callbackURL": callback,
                    "amount": f"{money(payment.amount):.2f}",
                    "currency": payment.currency,
                    "intent": "sale",
                    "merchantInvoiceNumber": payment.reference,
                },
                headers=self._headers(),
                timeout=TIMEOUT,
            )
        )

        redirect_url = data.get("bkashURL")
        if not redirect_url:
            raise PaymentError(
                data.get("statusMessage") or "bKash could not start this payment."
            )

        payment.provider_reference = data.get("paymentID", "")
        payment.raw_response = {"create": data}
        payment.save(update_fields=["provider_reference", "raw_response", "updated_at"])
        return PaymentIntent(payment=payment, redirect_url=redirect_url)

    def verify(self, payment, *, payload: dict | None = None) -> VerificationResult:
        """Execute, then query - both server-to-server."""
        self.ensure_configured()
        payment_id = payment.provider_reference or (payload or {}).get("paymentID")
        if not payment_id:
            return VerificationResult(
                status=PaymentStatus.FAILED, failure_reason="Missing bKash payment id."
            )

        data = self._json(
            requests.post(
                self._url("/tokenized/checkout/execute"),
                json={"paymentID": payment_id},
                headers=self._headers(),
                timeout=TIMEOUT,
            )
        )
        # An already-executed payment must be queried instead of re-executed.
        if data.get("statusCode") not in {"0000", None} or not data.get("trxID"):
            data = self._json(
                requests.post(
                    self._url("/tokenized/checkout/payment/status"),
                    json={"paymentID": payment_id},
                    headers=self._headers(),
                    timeout=TIMEOUT,
                )
            )

        return self._result_from(data, payment)

    def _result_from(self, data: dict, payment) -> VerificationResult:
        status = str(data.get("transactionStatus", "")).lower()
        if status == "completed" and data.get("trxID"):
            return VerificationResult(
                status=PaymentStatus.PAID,
                provider_reference=data["trxID"],
                amount=money(data.get("amount", payment.amount)),
                instrument_hint=self._mask(data.get("customerMsisdn", "")),
                raw=data,
            )
        if status in {"initiated", "pending"}:
            return VerificationResult(status=PaymentStatus.PENDING, raw=data)
        return VerificationResult(
            status=PaymentStatus.FAILED,
            failure_reason=data.get("statusMessage") or "bKash declined this payment.",
            raw=data,
        )

    @staticmethod
    def _mask(msisdn: str) -> str:
        """Never store a full phone number against a payment instrument."""
        digits = "".join(ch for ch in str(msisdn) if ch.isdigit())
        return f"bKash {digits[:3]}XXXXX{digits[-3:]}" if len(digits) >= 6 else "bKash"

    def refund(self, payment, *, amount: Decimal, reason: str = "") -> RefundResult:
        self.ensure_configured()
        data = self._json(
            requests.post(
                self._url("/tokenized/checkout/payment/refund"),
                json={
                    "paymentID": (payment.raw_response or {}).get("create", {}).get("paymentID", ""),
                    "trxID": payment.provider_reference,
                    "amount": f"{money(amount):.2f}",
                    "sku": payment.order.number,
                    "reason": (reason or "Order refund")[:255],
                },
                headers=self._headers(),
                timeout=TIMEOUT,
            )
        )
        completed = str(data.get("transactionStatus", "")).lower() == "completed"
        return RefundResult(
            status=PaymentStatus.REFUNDED if completed else PaymentStatus.FAILED,
            provider_reference=data.get("refundTrxID", ""),
            raw=data,
        )
