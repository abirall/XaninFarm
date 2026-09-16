"""Inventory models.

Stock is tracked per *batch*, not as a single counter, because XaninFarm sells
perishables. Every batch knows when it was produced, when it arrives and when
it expires, so the allocator can ship oldest-first and refuse expired goods.

Quantities are measured in variant units (one "unit" = one sellable pack) and
stored as Decimal - never float.

Three numbers describe a batch:
    quantity_received  - what arrived (immutable audit figure)
    quantity_available - free to sell right now
    quantity_reserved  - promised to an unfulfilled order, still on the shelf
    on hand            = available + reserved
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import F, Q, Sum
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel
from apps.core.utils import MONEY_FIELD, QUANTITY_FIELD

ZERO_QTY = Decimal("0.000")


class BatchStatus(models.TextChoices):
    ACTIVE = "active", _("Active")
    QUARANTINE = "quarantine", _("Quarantine")
    EXPIRED = "expired", _("Expired")
    DEPLETED = "depleted", _("Depleted")
    RECALLED = "recalled", _("Recalled")


class InventoryBatchQuerySet(models.QuerySet):
    def sellable(self, as_of: date | None = None, min_shelf_life_days: int = 0):
        """Batches that may legally be allocated to a new order.

        Excludes anything expired, quarantined, recalled or empty. This is the
        single definition of "sellable stock" - every caller goes through it.
        """
        as_of = as_of or timezone.localdate()
        cutoff = as_of + timedelta(days=min_shelf_life_days)
        return self.filter(
            status=BatchStatus.ACTIVE,
            quantity_available__gt=0,
        ).filter(Q(expiry_date__isnull=True) | Q(expiry_date__gte=cutoff))

    def fefo(self):
        """First-Expired-First-Out ordering.

        Deterministic across transactions, which matters: concurrent
        reservations acquire row locks in the same sequence, so they queue up
        instead of deadlocking.
        """
        return self.order_by(F("expiry_date").asc(nulls_last=True), "received_date", "id")

    def expired(self, as_of: date | None = None):
        as_of = as_of or timezone.localdate()
        return self.filter(expiry_date__isnull=False, expiry_date__lt=as_of)

    def expiring_within(self, days: int, as_of: date | None = None):
        as_of = as_of or timezone.localdate()
        return self.filter(
            status=BatchStatus.ACTIVE,
            quantity_available__gt=0,
            expiry_date__isnull=False,
            expiry_date__gte=as_of,
            expiry_date__lte=as_of + timedelta(days=days),
        )

    def on_hand_total(self) -> Decimal:
        totals = self.aggregate(
            available=Sum("quantity_available"), reserved=Sum("quantity_reserved")
        )
        return (totals["available"] or ZERO_QTY) + (totals["reserved"] or ZERO_QTY)


class InventoryBatch(TimeStampedModel):
    """A delivery of stock for one variant, tracked from receipt to depletion."""

    variant = models.ForeignKey(
        "products.ProductVariant",
        on_delete=models.PROTECT,
        related_name="batches",
        verbose_name=_("variant"),
    )
    batch_number = models.CharField(
        _("batch number"),
        max_length=60,
        help_text=_("Supplier or farm batch reference, e.g. MILK-2026-09-12-A."),
    )

    production_date = models.DateField(_("production date"), null=True, blank=True)
    received_date = models.DateField(_("received date"), default=timezone.localdate)
    expiry_date = models.DateField(
        _("expiry date"),
        null=True,
        blank=True,
        db_index=True,
        help_text=_("Required for perishable products. Expired stock is never sold."),
    )

    quantity_received = models.DecimalField(
        _("quantity received"),
        validators=[MinValueValidator(Decimal("0.000"))],
        **QUANTITY_FIELD,
    )
    quantity_available = models.DecimalField(
        _("quantity available"),
        default=ZERO_QTY,
        validators=[MinValueValidator(Decimal("0.000"))],
        **QUANTITY_FIELD,
    )
    quantity_reserved = models.DecimalField(
        _("quantity reserved"),
        default=ZERO_QTY,
        validators=[MinValueValidator(Decimal("0.000"))],
        **QUANTITY_FIELD,
    )

    unit_cost = models.DecimalField(
        _("unit cost"),
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.00"))],
        **MONEY_FIELD,
    )
    supplier = models.CharField(_("supplier / farm unit"), max_length=140, blank=True)
    storage_location = models.CharField(
        _("storage location"), max_length=80, blank=True, help_text=_("Cold room, rack, crate...")
    )
    status = models.CharField(
        _("status"), max_length=12, choices=BatchStatus.choices, default=BatchStatus.ACTIVE
    )
    notes = models.TextField(_("notes"), blank=True)

    objects = InventoryBatchQuerySet.as_manager()

    class Meta:
        verbose_name = _("inventory batch")
        verbose_name_plural = _("inventory batches")
        ordering = ("expiry_date", "received_date", "id")
        constraints = [
            models.UniqueConstraint(
                fields=["variant", "batch_number"], name="unique_batch_number_per_variant"
            ),
            # The database is the last line of defence against overselling:
            # no code path may drive a quantity negative.
            models.CheckConstraint(
                condition=Q(quantity_available__gte=0), name="batch_available_not_negative"
            ),
            models.CheckConstraint(
                condition=Q(quantity_reserved__gte=0), name="batch_reserved_not_negative"
            ),
            models.CheckConstraint(
                condition=Q(quantity_received__gte=0), name="batch_received_not_negative"
            ),
        ]
        indexes = [
            models.Index(fields=["variant", "status", "expiry_date"]),
            models.Index(fields=["status", "quantity_available"]),
        ]

    def __str__(self) -> str:
        return f"{self.variant.sku} / {self.batch_number}"

    def clean(self):
        errors = {}

        if self.variant_id and self.variant.product.is_perishable and not self.expiry_date:
            errors["expiry_date"] = _("Perishable products require an expiry date.")

        if self.expiry_date and self.production_date and self.expiry_date <= self.production_date:
            errors["expiry_date"] = _("Expiry date must be after the production date.")

        if self.expiry_date and self.expiry_date < self.received_date:
            errors["expiry_date"] = _("This batch is already expired on its received date.")

        if self.quantity_available + self.quantity_reserved > self.quantity_received:
            errors["quantity_available"] = _(
                "Available plus reserved quantity cannot exceed the quantity received."
            )

        if errors:
            raise ValidationError(errors)

    # --- Derived state --------------------------------------------------------
    @property
    def quantity_on_hand(self) -> Decimal:
        return self.quantity_available + self.quantity_reserved

    @property
    def quantity_shipped(self) -> Decimal:
        return self.quantity_received - self.quantity_on_hand

    @property
    def is_expired(self) -> bool:
        return bool(self.expiry_date and self.expiry_date < timezone.localdate())

    @property
    def days_to_expiry(self) -> int | None:
        if not self.expiry_date:
            return None
        return (self.expiry_date - timezone.localdate()).days

    @property
    def is_sellable(self) -> bool:
        return (
            self.status == BatchStatus.ACTIVE
            and not self.is_expired
            and self.quantity_available > 0
        )

    @property
    def expiry_state(self) -> str:
        """UI helper: expired | critical | soon | fresh | none."""
        days = self.days_to_expiry
        if days is None:
            return "none"
        if days < 0:
            return "expired"
        if days <= 1:
            return "critical"
        from django.conf import settings

        if days <= settings.EXPIRY_ALERT_DAYS:
            return "soon"
        return "fresh"


class StockMovement(TimeStampedModel):
    """Append-only inventory history.

    Every change to a batch writes one row here with the signed deltas and the
    resulting balances, so stock can always be reconciled and audited.
    """

    class Type(models.TextChoices):
        RECEIVE = "receive", _("Stock received")
        RESERVE = "reserve", _("Reserved for order")
        RELEASE = "release", _("Reservation released")
        SHIP = "ship", _("Shipped to customer")
        RETURN = "return", _("Returned to stock")
        ADJUST = "adjust", _("Manual adjustment")
        EXPIRE = "expire", _("Written off - expired")
        RECALL = "recall", _("Written off - recalled")

    batch = models.ForeignKey(
        InventoryBatch,
        on_delete=models.CASCADE,
        related_name="movements",
        verbose_name=_("batch"),
    )
    # Denormalised so per-variant reports do not need to join through batches.
    variant = models.ForeignKey(
        "products.ProductVariant",
        on_delete=models.CASCADE,
        related_name="stock_movements",
        verbose_name=_("variant"),
    )

    movement_type = models.CharField(_("type"), max_length=10, choices=Type.choices)
    quantity = models.DecimalField(_("quantity"), **QUANTITY_FIELD)

    available_delta = models.DecimalField(_("available change"), **QUANTITY_FIELD)
    reserved_delta = models.DecimalField(_("reserved change"), **QUANTITY_FIELD)
    balance_available = models.DecimalField(_("available after"), **QUANTITY_FIELD)
    balance_reserved = models.DecimalField(_("reserved after"), **QUANTITY_FIELD)

    reference_type = models.CharField(
        _("reference type"), max_length=30, blank=True, help_text=_("order, adjustment, task...")
    )
    reference_id = models.CharField(_("reference id"), max_length=64, blank=True)

    performed_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="stock_movements",
        verbose_name=_("performed by"),
    )
    note = models.CharField(_("note"), max_length=255, blank=True)

    class Meta:
        verbose_name = _("stock movement")
        verbose_name_plural = _("stock movements")
        ordering = ("-created_at", "-id")
        indexes = [
            models.Index(fields=["variant", "-created_at"]),
            models.Index(fields=["reference_type", "reference_id"]),
            models.Index(fields=["movement_type", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.get_movement_type_display()} {self.quantity} of {self.variant_id}"

    @classmethod
    def record(
        cls,
        *,
        batch: InventoryBatch,
        movement_type: str,
        quantity: Decimal,
        available_delta: Decimal,
        reserved_delta: Decimal,
        reference_type: str = "",
        reference_id: str | int = "",
        performed_by=None,
        note: str = "",
    ) -> StockMovement:
        """Write one history row. `batch` must already hold its new balances."""
        return cls.objects.create(
            batch=batch,
            variant_id=batch.variant_id,
            movement_type=movement_type,
            quantity=quantity,
            available_delta=available_delta,
            reserved_delta=reserved_delta,
            balance_available=batch.quantity_available,
            balance_reserved=batch.quantity_reserved,
            reference_type=reference_type,
            reference_id=str(reference_id or ""),
            performed_by=performed_by,
            note=note[:255],
        )


class StockReservation(TimeStampedModel):
    """Stock held for one order line, allocated from a specific batch.

    An order line may span several batches, so this is a many-rows-per-line
    record. It is what lets us release exactly what we took if the order is
    cancelled, and consume exactly what we took when it ships.
    """

    class Status(models.TextChoices):
        HELD = "held", _("Held")
        CONSUMED = "consumed", _("Consumed")
        RELEASED = "released", _("Released")

    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.CASCADE,
        related_name="stock_reservations",
        verbose_name=_("order"),
    )
    order_item = models.ForeignKey(
        "orders.OrderItem",
        on_delete=models.CASCADE,
        related_name="stock_reservations",
        null=True,
        blank=True,
        verbose_name=_("order item"),
    )
    batch = models.ForeignKey(
        InventoryBatch,
        on_delete=models.PROTECT,
        related_name="reservations",
        verbose_name=_("batch"),
    )
    variant = models.ForeignKey(
        "products.ProductVariant",
        on_delete=models.PROTECT,
        related_name="reservations",
        verbose_name=_("variant"),
    )

    quantity = models.DecimalField(
        _("quantity"), validators=[MinValueValidator(Decimal("0.001"))], **QUANTITY_FIELD
    )
    status = models.CharField(
        _("status"), max_length=10, choices=Status.choices, default=Status.HELD, db_index=True
    )
    expires_at = models.DateTimeField(
        _("hold expires at"),
        null=True,
        blank=True,
        help_text=_("Unpaid orders release their hold after this time."),
    )
    resolved_at = models.DateTimeField(_("resolved at"), null=True, blank=True)

    class Meta:
        verbose_name = _("stock reservation")
        verbose_name_plural = _("stock reservations")
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["order", "status"]),
            models.Index(fields=["status", "expires_at"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(quantity__gt=0), name="reservation_quantity_positive"
            )
        ]

    def __str__(self) -> str:
        return f"{self.quantity} x {self.variant_id} for order {self.order_id}"
