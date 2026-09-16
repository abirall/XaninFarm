"""Orders, order lines and the fulfilment timeline.

Two rules shape this module:

1. **Order status and payment status are separate columns.** A cash-on-delivery
   order can be packed and out for delivery while payment is still pending; a
   prepaid order can be paid before anyone has picked a box off the shelf.
   Nothing in the fulfilment flow reads or writes payment state except the
   explicit hand-offs in ``apps.orders.services``.

2. **Orders freeze money.** Unlike carts, which read live variant prices, every
   line stores the price it was sold at. Changing a product's price later never
   rewrites history.
"""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel
from apps.core.utils import MONEY_FIELD, ZERO, money, random_code, random_token
from apps.payments.constants import PaymentMethod, PaymentStatus


class OrderStatus(models.TextChoices):
    """Fulfilment stages. Cancelled and Returned are the two exits."""

    DRAFT = "draft", _("Draft")
    PENDING = "pending", _("Order placed")
    CONFIRMED = "confirmed", _("Confirmed")
    PREPARING = "preparing", _("Preparing")
    PACKED = "packed", _("Packed")
    OUT_FOR_DELIVERY = "out_for_delivery", _("Out for delivery")
    DELIVERED = "delivered", _("Delivered")
    CANCELLED = "cancelled", _("Cancelled")
    RETURNED = "returned", _("Returned")


#: The happy path, in order. Used to draw the tracking progress bar.
FULFILMENT_FLOW = (
    OrderStatus.PENDING,
    OrderStatus.CONFIRMED,
    OrderStatus.PREPARING,
    OrderStatus.PACKED,
    OrderStatus.OUT_FOR_DELIVERY,
    OrderStatus.DELIVERED,
)

#: Statuses where the order is finished, one way or another.
CLOSED_STATUSES = frozenset(
    {OrderStatus.DELIVERED, OrderStatus.CANCELLED, OrderStatus.RETURNED}
)

#: Statuses the customer may still cancel from.
CUSTOMER_CANCELLABLE = frozenset(
    {OrderStatus.PENDING, OrderStatus.CONFIRMED, OrderStatus.PREPARING}
)


def generate_order_number() -> str:
    """Human-friendly, non-sequential order number: ``XF-2609-K7QM3D``.

    Non-sequential on purpose - a guessable number leaks daily order volume
    and makes enumeration attacks easier.
    """
    prefix = getattr(settings, "ORDER_NUMBER_PREFIX", "XF")
    stamp = timezone.localdate().strftime("%y%m")
    for _attempt in range(10):
        number = f"{prefix}-{stamp}-{random_code(6)}"
        if not Order.objects.filter(number=number).exists():
            return number
    # Astronomically unlikely; fall back to a longer code rather than loop.
    return f"{prefix}-{stamp}-{random_code(10)}"


class OrderQuerySet(models.QuerySet):
    def placed(self):
        """Real orders - excludes drafts."""
        return self.exclude(status=OrderStatus.DRAFT)

    def open(self):
        return self.placed().exclude(status__in=CLOSED_STATUSES)

    def for_user(self, user):
        return self.filter(user=user).placed()

    def with_lines(self):
        return self.prefetch_related("items", "items__variant", "items__variant__product")


class Order(TimeStampedModel):
    number = models.CharField(_("order number"), max_length=32, unique=True, editable=False)

    # PROTECT: a customer with order history must be deactivated, never deleted.
    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="orders",
        verbose_name=_("customer"),
    )

    status = models.CharField(
        _("order status"),
        max_length=20,
        choices=OrderStatus.choices,
        default=OrderStatus.PENDING,
        db_index=True,
    )
    payment_status = models.CharField(
        _("payment status"),
        max_length=20,
        choices=PaymentStatus.choices,
        default=PaymentStatus.PENDING,
        db_index=True,
        help_text=_("Tracked separately from the fulfilment status."),
    )
    payment_method = models.CharField(
        _("payment method"), max_length=20, choices=PaymentMethod.choices, default=PaymentMethod.COD
    )

    # --- Contact -------------------------------------------------------------
    contact_email = models.EmailField(_("contact email"))
    contact_phone = models.CharField(_("contact phone"), max_length=20)

    # --- Shipping address snapshot -------------------------------------------
    # Copied at checkout so editing the saved address never rewrites an order.
    address = models.ForeignKey(
        "accounts.Address",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orders",
        verbose_name=_("saved address"),
    )
    ship_recipient = models.CharField(_("recipient"), max_length=150)
    ship_phone = models.CharField(_("delivery phone"), max_length=20)
    ship_alternate_phone = models.CharField(_("alternate phone"), max_length=20, blank=True)
    ship_division = models.CharField(_("division"), max_length=40)
    ship_district = models.CharField(_("district"), max_length=80)
    ship_area = models.CharField(_("area"), max_length=120)
    ship_address_line = models.CharField(_("street address"), max_length=255)
    ship_postcode = models.CharField(_("postcode"), max_length=10, blank=True)
    ship_note = models.CharField(_("delivery note"), max_length=200, blank=True)

    # --- Delivery ------------------------------------------------------------
    delivery_zone = models.ForeignKey(
        "delivery.DeliveryZone",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orders",
        verbose_name=_("delivery zone"),
    )
    delivery_slot = models.ForeignKey(
        "delivery.DeliverySlot",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orders",
        verbose_name=_("delivery slot"),
    )
    delivery_partner = models.ForeignKey(
        "delivery.DeliveryPartner",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orders",
        verbose_name=_("rider"),
    )
    requested_delivery_date = models.DateField(_("requested delivery date"), null=True, blank=True)
    tracking_note = models.CharField(_("tracking note"), max_length=200, blank=True)

    # --- Money (all server-computed) -----------------------------------------
    subtotal = models.DecimalField(_("subtotal"), default=Decimal("0.00"), **MONEY_FIELD)
    discount_amount = models.DecimalField(_("discount"), default=Decimal("0.00"), **MONEY_FIELD)
    delivery_fee = models.DecimalField(_("delivery fee"), default=Decimal("0.00"), **MONEY_FIELD)
    delivery_discount = models.DecimalField(
        _("delivery discount"), default=Decimal("0.00"), **MONEY_FIELD
    )
    total = models.DecimalField(
        _("total"),
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
        **MONEY_FIELD,
    )
    amount_paid = models.DecimalField(_("amount paid"), default=Decimal("0.00"), **MONEY_FIELD)
    amount_refunded = models.DecimalField(
        _("amount refunded"), default=Decimal("0.00"), **MONEY_FIELD
    )

    coupon = models.ForeignKey(
        "coupons.Coupon",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orders",
        verbose_name=_("coupon"),
    )
    coupon_code = models.CharField(_("coupon code"), max_length=32, blank=True)

    # --- Notes and audit ------------------------------------------------------
    customer_note = models.TextField(_("customer note"), max_length=500, blank=True)
    staff_note = models.TextField(_("internal note"), blank=True)
    cancellation_reason = models.CharField(_("cancellation reason"), max_length=200, blank=True)

    access_token = models.CharField(
        _("access token"),
        max_length=48,
        default=random_token,
        editable=False,
        help_text=_("Unguessable id used in emailed tracking links."),
    )
    ip_address = models.GenericIPAddressField(_("IP address"), null=True, blank=True)

    placed_at = models.DateTimeField(_("placed at"), default=timezone.now, db_index=True)
    confirmed_at = models.DateTimeField(_("confirmed at"), null=True, blank=True)
    packed_at = models.DateTimeField(_("packed at"), null=True, blank=True)
    dispatched_at = models.DateTimeField(_("dispatched at"), null=True, blank=True)
    delivered_at = models.DateTimeField(_("delivered at"), null=True, blank=True)
    cancelled_at = models.DateTimeField(_("cancelled at"), null=True, blank=True)

    objects = OrderQuerySet.as_manager()

    class Meta:
        ordering = ("-placed_at", "-id")
        verbose_name = _("order")
        verbose_name_plural = _("orders")
        indexes = [
            models.Index(fields=["user", "-placed_at"]),
            models.Index(fields=["status", "-placed_at"]),
            models.Index(fields=["payment_status", "-placed_at"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(total__gte=Decimal("0.00")), name="order_total_non_negative"
            ),
            models.CheckConstraint(
                condition=models.Q(amount_paid__gte=Decimal("0.00")),
                name="order_amount_paid_non_negative",
            ),
        ]

    def __str__(self) -> str:
        return self.number

    def save(self, *args, **kwargs):
        if not self.number:
            self.number = generate_order_number()
        super().save(*args, **kwargs)

    def get_absolute_url(self) -> str:
        return reverse("orders:detail", kwargs={"number": self.number})

    def get_tracking_url(self) -> str:
        return reverse("orders:track", kwargs={"number": self.number, "token": self.access_token})

    # --- Money helpers --------------------------------------------------------
    @property
    def goods_total(self) -> Decimal:
        return money(self.subtotal - self.discount_amount)

    @property
    def balance_due(self) -> Decimal:
        return max(ZERO, money(self.total - self.amount_paid + self.amount_refunded))

    @property
    def is_paid(self) -> bool:
        return self.payment_status == PaymentStatus.PAID

    @property
    def is_cod(self) -> bool:
        return self.payment_method == PaymentMethod.COD

    @property
    def total_savings(self) -> Decimal:
        return money(self.discount_amount + self.delivery_discount)

    # --- Status helpers -------------------------------------------------------
    @property
    def is_closed(self) -> bool:
        return self.status in CLOSED_STATUSES

    @property
    def is_cancellable_by_customer(self) -> bool:
        return self.status in CUSTOMER_CANCELLABLE and not self.is_paid

    @property
    def progress_index(self) -> int:
        """Position on the tracking bar, or -1 for cancelled/returned orders."""
        try:
            return FULFILMENT_FLOW.index(OrderStatus(self.status))
        except ValueError:
            return -1

    @property
    def progress_percent(self) -> int:
        index = self.progress_index
        if index < 0:
            return 0
        return round(index / (len(FULFILMENT_FLOW) - 1) * 100)

    @property
    def fulfilment_steps(self) -> list[dict]:
        """The tracking bar, ready to render.

        Lives here rather than in the templates so the customer's order page
        and the public tracking page cannot drift apart. A cancelled order has
        a progress index of -1, so every step comes back undone.
        """
        index = self.progress_index
        return [
            {
                "label": OrderStatus(status).label,
                "is_done": index >= position,
                "is_current": index == position,
            }
            for position, status in enumerate(FULFILMENT_FLOW)
        ]

    @property
    def shipping_address_lines(self) -> list[str]:
        return [
            self.ship_recipient,
            self.ship_address_line,
            f"{self.ship_area}, {self.ship_district}",
            f"{self.ship_division}{f' - {self.ship_postcode}' if self.ship_postcode else ''}",
            self.ship_phone,
        ]

    @property
    def item_count(self) -> int:
        return sum(item.quantity for item in self.items.all())


class OrderItem(TimeStampedModel):
    """A sold line. Prices and names are snapshots, never live lookups."""

    order = models.ForeignKey(
        Order, on_delete=models.CASCADE, related_name="items", verbose_name=_("order")
    )
    # PROTECT keeps the catalogue honest: a variant that has been sold cannot
    # be deleted, only deactivated.
    variant = models.ForeignKey(
        "products.ProductVariant",
        on_delete=models.PROTECT,
        related_name="order_items",
        verbose_name=_("variant"),
    )

    product_name = models.CharField(_("product"), max_length=200)
    variant_name = models.CharField(_("variant"), max_length=120)
    sku = models.CharField(_("SKU"), max_length=64, blank=True)

    unit_price = models.DecimalField(_("unit price"), **MONEY_FIELD)
    quantity = models.PositiveIntegerField(_("quantity"), default=1)
    line_total = models.DecimalField(_("line total"), **MONEY_FIELD)

    class Meta:
        ordering = ("id",)
        verbose_name = _("order item")
        verbose_name_plural = _("order items")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gte=1), name="order_item_quantity_positive"
            ),
            models.CheckConstraint(
                condition=models.Q(unit_price__gte=Decimal("0.00")),
                name="order_item_price_non_negative",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.quantity} x {self.product_name} ({self.variant_name})"

    @property
    def display_name(self) -> str:
        return f"{self.product_name} - {self.variant_name}"


class OrderEvent(TimeStampedModel):
    """Append-only fulfilment timeline, shown on the tracking page."""

    order = models.ForeignKey(
        Order, on_delete=models.CASCADE, related_name="events", verbose_name=_("order")
    )
    status = models.CharField(_("status"), max_length=20, choices=OrderStatus.choices)
    note = models.CharField(_("note"), max_length=300, blank=True)
    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="order_events",
        verbose_name=_("changed by"),
    )
    is_customer_visible = models.BooleanField(_("visible to customer"), default=True)

    class Meta:
        ordering = ("created_at", "id")
        verbose_name = _("order event")
        verbose_name_plural = _("order events")
        indexes = [models.Index(fields=["order", "created_at"])]

    def __str__(self) -> str:
        return f"{self.order_id}: {self.get_status_display()}"
