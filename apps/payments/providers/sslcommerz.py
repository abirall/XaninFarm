"""SSLCommerz (hosted checkout).

Flow:

1. ``POST /gwprocess/v4/api.php``        -> GatewayPageURL
2. customer pays on the hosted page and is returned to ``success_url``
3. ``GET  /validator/api/validationserverAPI.php?val_id=...`` -> the only
   answer we trust. The POST-back fields and the IPN are both re-validated
   against this call, so a forged form post buys an attacker nothing.

SSLCommerz returns card numbers already masked; we keep only the brand and
the last four digits and never persist the rest.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from decimal import Decimal

import requests
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

logger = logging.getLogger(__name__)

TIMEOUT = 20
LIVE_HOST = "https://securepay.sslcommerz.com"
SANDBOX_HOST = "https://sandbox.sslcommerz.com"
VALID_STATUSES = {"VALID", "VALIDATED"}


class SslcommerzProvider(PaymentProvider):
    code = PaymentMethod.SSLCOMMERZ
    label = "Card / Mobile banking"
    redirects_offsite = True
    supports_refund = True
    required_config = ("store_id", "store_password")

    @property
    def host(self) -> str:
        return SANDBOX_HOST if self.config.get("sandbox") else LIVE_HOST

    @property
    def _credentials(self) -> dict:
        return {
            "store_id": self.config["store_id"],
            "store_passwd": self.config["store_password"],
        }

    @staticmethod
    def _json(response) -> dict:
        try:
            return response.json()
        except ValueError as exc:
            logger.error("SSLCommerz returned non-JSON (%s)", response.status_code)
            raise PaymentError("The card gateway is not responding right now.") from exc

    # --- Flow -----------------------------------------------------------------
    def initiate(self, payment, *, request=None) -> PaymentIntent:
        self.ensure_configured()
        order = payment.order

        def absolute(name: str) -> str:
            if request is None:
                return ""
            return request.build_absolute_uri(reverse(name, kwargs={"number": order.number}))

        payload = {
            **self._credentials,
            "total_amount": f"{money(payment.amount):.2f}",
            "currency": payment.currency,
            "tran_id": payment.reference,
            "success_url": absolute("payments:return"),
            "fail_url": absolute("payments:return"),
            "cancel_url": absolute("payments:cancel"),
            "ipn_url": request.build_absolute_uri(
                reverse("payments:webhook", kwargs={"provider": self.code})
            )
            if request is not None
            else "",
            "cus_name": order.ship_recipient,
            "cus_email": order.contact_email or "noreply@xaninfarms.com",
            "cus_phone": order.ship_phone or order.contact_phone,
            "cus_add1": order.ship_address_line,
            "cus_city": order.ship_district,
            "cus_postcode": order.ship_postcode,
            "cus_country": "Bangladesh",
            "shipping_method": "Courier",
            "num_of_item": order.items.count(),
            "product_name": f"XaninFarm order {order.number}",
            "product_category": "Groceries",
            "product_profile": "physical-goods",
        }
        data = self._json(
            requests.post(
                f"{self.host}/gwprocess/v4/api.php", data=payload, timeout=TIMEOUT
            )
        )
        redirect_url = data.get("GatewayPageURL")
        if str(data.get("status", "")).upper() != "SUCCESS" or not redirect_url:
            raise PaymentError(
                data.get("failedreason") or "The card gateway could not start this payment."
            )

        payment.provider_reference = data.get("sessionkey", "")
        payment.raw_response = {"session": data}
        payment.save(update_fields=["provider_reference", "raw_response", "updated_at"])
        return PaymentIntent(payment=payment, redirect_url=redirect_url)

    def verify(self, payment, *, payload: dict | None = None) -> VerificationResult:
        """Re-validate against SSLCommerz. The posted-back fields are only ever
        used to find the ``val_id``; the status comes from the validation API."""
        self.ensure_configured()
        payload = payload or {}
        val_id = str(payload.get("val_id") or "").strip()
        if not val_id:
            return VerificationResult(
                status=PaymentStatus.FAILED,
                failure_reason="The gateway did not return a validation id.",
                raw={},
            )

        data = self._json(
            requests.get(
                f"{self.host}/validator/api/validationserverAPI.php",
                params={**self._credentials, "val_id": val_id, "format": "json"},
                timeout=TIMEOUT,
            )
        )
        status = str(data.get("status", "")).upper()

        # The gateway must agree about which transaction this was.
        if data.get("tran_id") and data["tran_id"] != payment.reference:
            logger.warning(
                "SSLCommerz val_id %s belongs to %s, not %s",
                val_id,
                data.get("tran_id"),
                payment.reference,
            )
            return VerificationResult(
                status=PaymentStatus.FAILED,
                failure_reason="This payment could not be matched to your order.",
                raw=data,
            )

        if status in VALID_STATUSES:
            return VerificationResult(
                status=PaymentStatus.PAID,
                provider_reference=data.get("bank_tran_id") or val_id,
                amount=money(data.get("amount", payment.amount)),
                instrument_hint=self._hint(data),
                raw=data,
            )
        if status in {"PENDING", "PROCESSING"}:
            return VerificationResult(status=PaymentStatus.PENDING, raw=data)
        if status == "CANCELLED":
            return VerificationResult(
                status=PaymentStatus.CANCELLED, failure_reason="Payment cancelled.", raw=data
            )
        return VerificationResult(
            status=PaymentStatus.FAILED,
            failure_reason=data.get("error") or "The card gateway declined this payment.",
            raw=data,
        )

    @staticmethod
    def _hint(data: dict) -> str:
        """Brand plus last four only - never the number SSLCommerz sent back."""
        brand = str(data.get("card_brand") or data.get("card_type") or "Card").split("-")[0]
        digits = "".join(ch for ch in str(data.get("card_no", "")) if ch.isdigit())
        return f"{brand.strip()} ****{digits[-4:]}" if len(digits) >= 4 else brand.strip()

    # --- IPN ------------------------------------------------------------------
    def parse_webhook(self, request) -> WebhookResult:
        """Check SSLCommerz's ``verify_sign`` before the payload is looked at.

        The signature only proves the message is ours; the caller still runs
        ``verify()`` against the validation API before any money is recorded.
        """
        self.ensure_configured()
        posted = request.POST.dict()

        verify_key = posted.get("verify_key", "")
        verify_sign = posted.get("verify_sign", "")
        if not verify_key or not verify_sign:
            raise SignatureError("The gateway notification was not signed.")

        # md5 is SSLCommerz's prescribed scheme, not a choice of ours.
        pairs = [f"{key}={posted.get(key, '')}" for key in verify_key.split(",")]
        hashed_password = hashlib.md5(
            str(self.config["store_password"]).encode()
        ).hexdigest()
        pairs.append(f"store_passwd={hashed_password}")
        expected = hashlib.md5("&".join(sorted(pairs)).encode()).hexdigest()
        if not hmac.compare_digest(expected, verify_sign):
            raise SignatureError("The gateway notification signature did not match.")

        val_id = str(posted.get("val_id") or "").strip()
        if not val_id:
            raise SignatureError("The gateway notification had no validation id.")

        status = str(posted.get("status", "")).upper()
        mapped = {
            "VALID": PaymentStatus.PAID,
            "VALIDATED": PaymentStatus.PAID,
            "FAILED": PaymentStatus.FAILED,
            "CANCELLED": PaymentStatus.CANCELLED,
        }.get(status, PaymentStatus.PENDING)
        amount = posted.get("amount")
        return WebhookResult(
            event_id=val_id,
            payment_reference=str(posted.get("tran_id") or ""),
            provider_reference=str(posted.get("bank_tran_id") or ""),
            status=mapped,
            amount=money(amount) if amount else None,
            raw=posted,
        )

    def refund(self, payment, *, amount: Decimal, reason: str = "") -> RefundResult:
        self.ensure_configured()
        bank_tran_id = payment.provider_reference
        if not bank_tran_id:
            raise PaymentError("This payment has no bank transaction id to refund against.")

        data = self._json(
            requests.get(
                f"{self.host}/validator/api/merchantTransIDvalidationAPI.php",
                params={
                    **self._credentials,
                    "bank_tran_id": bank_tran_id,
                    "refund_amount": f"{money(amount):.2f}",
                    "refund_remarks": (reason or "Order refund")[:255],
                    "format": "json",
                },
                timeout=TIMEOUT,
            )
        )
        accepted = str(data.get("status", "")).lower() in {"success", "processing"}
        return RefundResult(
            status=PaymentStatus.REFUNDED if accepted else PaymentStatus.FAILED,
            provider_reference=data.get("refund_ref_id", ""),
            raw=data,
        )
