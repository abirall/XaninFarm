"""Payment records, refunds and the webhook ledger.

**No raw card data is ever stored.** Providers hold the instrument; we keep
their reference plus a masked hint (``bKash 01XXXXX789``) and nothing more.
Anything a provider sends back lands in ``raw_response``, which is scrubbed of
known sensitive keys before it is saved.
"""

from __future__ import annotations

from decimal import Decimal

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel
from apps.core.utils import MONEY_FIELD, ZERO, money, random_token
from apps.payments.constants import PaymentMethod, PaymentStatus, TransactionKind

#: Keys that must never be persisted, whatever a provider echoes back.
SENSITIVE_KEYS = frozenset(
    {
        "card",
        "cardnumber",
        "card_number",
        "cardno",
        "cvv",
        "cvc",
        "pin",
        "password",
        "expiry",
        "expiry_date",
        "app_key",
        "app_secret",
        "username",
        "id_token",
        "refresh_token",
        "authorization",
        "signature_key",
        "store_passwd",
    }
)


def scrub(payload) -> dict | list | str | int | float | bool | None:
    """Recursively drop sensitive keys from a provider payload."""
    if isinstance(payload, dict):
        return {
            key: ("[redacted]" if str(key).lower() in SENSITIVE_KEYS else scrub(value))
            for key, value in payload.items()
        }
    if isinstance(payload, list):
        return [scrub(item) for item in payload]
    return payload


def generate_reference() -> str:
    """Our own id for a payment attempt, sent to the gateway as the merchant ref."""
    return f"PAY{random_token(18).upper()}"


class Payment(TimeStampedModel):
    """One attempt to collect money for an order.

    An order may have several: a failed bKash attempt followed by a
    successful one, or a COD collection recorded at the door.
    """

    order = models.ForeignKey(
        "orders.Order", on_delete=models.PROTECT, related_name="payments", verbose_name=_("order")
    )
    provider = models.CharField(
        _("provider"), max_length=20, choices=PaymentMethod.choices, db_index=True
    )

    reference = models.CharField(
        _("our reference"),
        max_length=40,
        unique=True,
        default=generate_reference,
        editable=False,
        help_text=_("Merchant reference sent to the gateway."),
    )
    provider_reference = models.CharField(
        _("provider reference"),
        max_length=120,
        blank=True,
        db_index=True,
        help_text=_("The gateway's transaction id."),
    )
    instrument_hint = models.CharField(
        _("instrument"),
        max_length=60,
        blank=True,
        help_text=_("Masked description only, e.g. 'bKash 01XXXXXX789'."),
    )

    amount = models.DecimalField(_("amount"), **MONEY_FIELD)
    currency = models.CharField(_("currency"), max_length=3, default="BDT")
    status = models.CharField(
        _("status"),
        max_length=20,
        choices=PaymentStatus.choices,
        default=PaymentStatus.PENDING,
        db_index=True,
    )

    failure_reason = models.CharField(_("failure reason"), max_length=255, blank=True)
    raw_response = models.JSONField(_("provider response"), default=dict, blank=True)

    initiated_at = models.DateTimeField(_("initiated at"), default=timezone.now)
    paid_at = models.DateTimeField(_("paid at"), null=True, blank=True)
    failed_at = models.DateTimeField(_("failed at"), null=True, blank=True)

    recorded_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="recorded_payments",
        verbose_name=_("recorded by"),
        help_text=_("Set for cash collected by a rider or entered by staff."),
    )

    class Meta:
        ordering = ("-created_at",)
        verbose_name = _("payment")
        verbose_name_plural = _("payments")
        indexes = [
            models.Index(fields=["order", "status"]),
            models.Index(fields=["provider", "status"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gte=Decimal("0.00")), name="payment_amount_non_negative"
            )
        ]

    def __str__(self) -> str:
        return f"{self.reference} ({self.get_provider_display()})"

    def save(self, *args, **kwargs):
        if self.raw_response:
            self.raw_response = scrub(self.raw_response)
        super().save(*args, **kwargs)

    @property
    def is_paid(self) -> bool:
        return self.status == PaymentStatus.PAID

    @property
    def is_open(self) -> bool:
        """Still worth polling or completing."""
        return self.status in {PaymentStatus.PENDING, PaymentStatus.AUTHORIZED}

    @property
    def refunded_amount(self) -> Decimal:
        total = self.refunds.filter(status=PaymentStatus.REFUNDED).aggregate(
            total=models.Sum("amount")
        )["total"]
        return money(total or ZERO)

    @property
    def refundable_amount(self) -> Decimal:
        # A part-refunded payment still has money left to give back.
        if self.status not in {PaymentStatus.PAID, PaymentStatus.PARTIALLY_REFUNDED}:
            return ZERO
        return max(ZERO, money(self.amount - self.refunded_amount))


class Refund(TimeStampedModel):
    payment = models.ForeignKey(
        Payment, on_delete=models.PROTECT, related_name="refunds", verbose_name=_("payment")
    )
    amount = models.DecimalField(_("amount"), **MONEY_FIELD)
    reason = models.CharField(_("reason"), max_length=255, blank=True)
    provider_reference = models.CharField(_("provider reference"), max_length=120, blank=True)
    status = models.CharField(
        _("status"),
        max_length=20,
        choices=PaymentStatus.choices,
        default=PaymentStatus.PENDING,
    )
    raw_response = models.JSONField(_("provider response"), default=dict, blank=True)
    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="issued_refunds",
        verbose_name=_("issued by"),
    )

    class Meta:
        ordering = ("-created_at",)
        verbose_name = _("refund")
        verbose_name_plural = _("refunds")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=Decimal("0.00")), name="refund_amount_positive"
            )
        ]

    def __str__(self) -> str:
        return f"Refund {self.amount} on {self.payment_id}"

    def save(self, *args, **kwargs):
        if self.raw_response:
            self.raw_response = scrub(self.raw_response)
        super().save(*args, **kwargs)


class WebhookEvent(TimeStampedModel):
    """Every callback a provider sends us, stored before it is acted on.

    The ``(provider, event_id)`` unique constraint is the idempotency guard:
    a gateway that retries the same notification five times still moves the
    money exactly once.
    """

    class Status(models.TextChoices):
        RECEIVED = "received", _("Received")
        PROCESSED = "processed", _("Processed")
        DUPLICATE = "duplicate", _("Duplicate")
        REJECTED = "rejected", _("Rejected")
        FAILED = "failed", _("Failed")

    provider = models.CharField(_("provider"), max_length=20, db_index=True)
    event_id = models.CharField(
        _("event id"),
        max_length=160,
        help_text=_("The provider's own id for this notification."),
    )
    kind = models.CharField(
        _("kind"), max_length=20, choices=TransactionKind.choices, default=TransactionKind.PAYMENT
    )

    payment = models.ForeignKey(
        Payment,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="webhook_events",
        verbose_name=_("payment"),
    )
    signature_valid = models.BooleanField(_("signature verified"), default=False)
    status = models.CharField(
        _("status"), max_length=12, choices=Status.choices, default=Status.RECEIVED, db_index=True
    )
    payload = models.JSONField(_("payload"), default=dict, blank=True)
    error = models.CharField(_("error"), max_length=255, blank=True)
    processed_at = models.DateTimeField(_("processed at"), null=True, blank=True)
    source_ip = models.GenericIPAddressField(_("source IP"), null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = _("webhook event")
        verbose_name_plural = _("webhook events")
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "event_id"], name="unique_webhook_event_per_provider"
            )
        ]
        indexes = [models.Index(fields=["provider", "status", "-created_at"])]

    def __str__(self) -> str:
        return f"{self.provider}:{self.event_id}"

    def save(self, *args, **kwargs):
        if self.payload:
            self.payload = scrub(self.payload)
        super().save(*args, **kwargs)
