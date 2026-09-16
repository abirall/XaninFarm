"""Payment vocabulary.

Kept free of model imports so ``apps.orders`` can use these choices without
creating a circular dependency.
"""

from __future__ import annotations

from django.db import models
from django.utils.translation import gettext_lazy as _


class PaymentStatus(models.TextChoices):
    """Where the money is. Deliberately independent of order status."""

    PENDING = "pending", _("Awaiting payment")
    AUTHORIZED = "authorized", _("Authorized")
    PAID = "paid", _("Paid")
    PARTIALLY_REFUNDED = "partially_refunded", _("Partially refunded")
    REFUNDED = "refunded", _("Refunded")
    FAILED = "failed", _("Failed")
    CANCELLED = "cancelled", _("Cancelled")


class PaymentMethod(models.TextChoices):
    COD = "cod", _("Cash on delivery")
    MOCK = "mock", _("Sandbox checkout")
    BKASH = "bkash", _("bKash")
    NAGAD = "nagad", _("Nagad")
    SSLCOMMERZ = "sslcommerz", _("Card or bank (SSLCommerz)")


class TransactionKind(models.TextChoices):
    PAYMENT = "payment", _("Payment")
    REFUND = "refund", _("Refund")


#: Statuses that mean the order is fully settled.
SETTLED_STATUSES = frozenset({PaymentStatus.PAID, PaymentStatus.REFUNDED})

#: Statuses a provider may never move away from without a new transaction.
TERMINAL_STATUSES = frozenset(
    {PaymentStatus.REFUNDED, PaymentStatus.CANCELLED, PaymentStatus.FAILED}
)
