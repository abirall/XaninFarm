"""Nagad Payment Gateway (checkout API).

Flow:

1. ``POST /api/dfs/check-out/initialize/{merchant}/{order}`` -> challenge +
   paymentReferenceId
2. ``POST /api/dfs/check-out/complete/{paymentReferenceId}`` -> callBackUrl
3. customer authorises on callBackUrl and returns to us
4. ``GET  /api/dfs/verify/payment/{paymentReferenceId}``     -> authoritative
   status, which is the only thing ``verify`` trusts

Steps 1 and 2 carry an RSA-encrypted ``sensitiveData`` blob signed with the
merchant private key. Those keys are issued by Nagad during onboarding; until
they are present in the environment the provider refuses to start a payment
rather than pretending to work.
"""

from __future__ import annotations

import base64
import json
import logging

import requests
from django.urls import reverse
from django.utils import timezone

from apps.core.utils import money
from apps.payments.constants import PaymentMethod, PaymentStatus
from apps.payments.providers.base import (
    PaymentError,
    PaymentIntent,
    PaymentProvider,
    ProviderNotConfigured,
    VerificationResult,
)

logger = logging.getLogger(__name__)

TIMEOUT = 20


class NagadProvider(PaymentProvider):
    code = PaymentMethod.NAGAD
    label = "Nagad"
    redirects_offsite = True
    supports_refund = False  # Nagad refunds are raised through merchant support.
    required_config = ("base_url", "merchant_id", "merchant_private_key", "pg_public_key")

    # --- Crypto ---------------------------------------------------------------
    @staticmethod
    def _crypto():
        try:
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import padding
        except ImportError as exc:  # pragma: no cover - depends on the install
            raise ProviderNotConfigured(
                "Nagad needs the 'cryptography' package to sign requests."
            ) from exc
        return hashes, serialization, padding

    @staticmethod
    def _pem(key: str, *, public: bool) -> bytes:
        """Accept either a bare base64 key body or a full PEM block."""
        key = str(key).strip()
        if "-----BEGIN" in key:
            return key.encode()
        label = "PUBLIC KEY" if public else "PRIVATE KEY"
        return f"-----BEGIN {label}-----\n{key}\n-----END {label}-----".encode()

    def _encrypt(self, payload: dict) -> str:
        hashes, serialization, padding = self._crypto()
        public_key = serialization.load_pem_public_key(
            self._pem(self.config["pg_public_key"], public=True)
        )
        blob = public_key.encrypt(json.dumps(payload).encode(), padding.PKCS1v15())
        return base64.b64encode(blob).decode()

    def _sign(self, payload: dict) -> str:
        hashes, serialization, padding = self._crypto()
        private_key = serialization.load_pem_private_key(
            self._pem(self.config["merchant_private_key"], public=False), password=None
        )
        signature = private_key.sign(
            json.dumps(payload).encode(), padding.PKCS1v15(), hashes.SHA256()
        )
        return base64.b64encode(signature).decode()

    # --- Plumbing -------------------------------------------------------------
    def _url(self, path: str) -> str:
        return f"{str(self.config['base_url']).rstrip('/')}{path}"

    def _headers(self, request=None) -> dict:
        ip = "0.0.0.0"
        if request is not None:
            ip = request.META.get("REMOTE_ADDR", ip)
        return {
            "Content-Type": "application/json",
            "X-KM-Api-Version": "v-0.2.0",
            "X-KM-IP-V4": ip,
            "X-KM-Client-Type": "PC_WEB",
        }

    @staticmethod
    def _json(response) -> dict:
        try:
            data = response.json()
        except ValueError as exc:
            logger.error("Nagad returned non-JSON (%s)", response.status_code)
            raise PaymentError("Nagad is not responding correctly right now.") from exc
        return data

    # --- Flow -----------------------------------------------------------------
    def initiate(self, payment, *, request=None) -> PaymentIntent:
        self.ensure_configured()

        stamp = timezone.localtime().strftime("%Y%m%d%H%M%S")
        merchant = str(self.config["merchant_id"])
        order_id = payment.reference

        sensitive = {
            "merchantId": merchant,
            "datetime": stamp,
            "orderId": order_id,
            "challenge": payment.reference,
        }
        init = self._json(
            requests.post(
                self._url(f"/api/dfs/check-out/initialize/{merchant}/{order_id}"),
                json={
                    "accountNumber": "",
                    "dateTime": stamp,
                    "sensitiveData": self._encrypt(sensitive),
                    "signature": self._sign(sensitive),
                },
                headers=self._headers(request),
                timeout=TIMEOUT,
            )
        )
        reference_id = init.get("paymentReferenceId")
        if not reference_id:
            raise PaymentError(init.get("message") or "Nagad could not start this payment.")

        callback = (
            request.build_absolute_uri(
                reverse("payments:return", kwargs={"number": payment.order.number})
            )
            if request is not None
            else ""
        )
        order_data = {
            "merchantId": merchant,
            "orderId": order_id,
            "currencyCode": "050",  # BDT
            "amount": f"{money(payment.amount):.2f}",
            "challenge": init.get("challenge", ""),
        }
        complete = self._json(
            requests.post(
                self._url(f"/api/dfs/check-out/complete/{reference_id}"),
                json={
                    "sensitiveData": self._encrypt(order_data),
                    "signature": self._sign(order_data),
                    "merchantCallbackURL": callback,
                },
                headers=self._headers(request),
                timeout=TIMEOUT,
            )
        )
        redirect_url = complete.get("callBackUrl")
        if not redirect_url:
            raise PaymentError(
                complete.get("message") or "Nagad could not start this payment."
            )

        payment.provider_reference = reference_id
        payment.raw_response = {"initialize": init, "complete": complete}
        payment.save(update_fields=["provider_reference", "raw_response", "updated_at"])
        return PaymentIntent(payment=payment, redirect_url=redirect_url)

    def verify(self, payment, *, payload: dict | None = None) -> VerificationResult:
        """Ask Nagad directly. The ``?status=`` we were redirected with is ignored."""
        self.ensure_configured()
        reference_id = payment.provider_reference or (payload or {}).get("payment_ref_id")
        if not reference_id:
            return VerificationResult(
                status=PaymentStatus.FAILED, failure_reason="Missing Nagad reference."
            )

        data = self._json(
            requests.get(
                self._url(f"/api/dfs/verify/payment/{reference_id}"), timeout=TIMEOUT
            )
        )
        status = str(data.get("status", "")).lower()
        if status == "success":
            return VerificationResult(
                status=PaymentStatus.PAID,
                provider_reference=data.get("issuerPaymentRefNo") or reference_id,
                amount=money(data.get("amount", payment.amount)),
                instrument_hint="Nagad wallet",
                raw=data,
            )
        if status in {"initiated", "pending", "in_progress"}:
            return VerificationResult(status=PaymentStatus.PENDING, raw=data)
        if status in {"aborted", "cancelled"}:
            return VerificationResult(
                status=PaymentStatus.CANCELLED, failure_reason="Payment cancelled.", raw=data
            )
        return VerificationResult(
            status=PaymentStatus.FAILED,
            failure_reason=data.get("message") or "Nagad declined this payment.",
            raw=data,
        )
