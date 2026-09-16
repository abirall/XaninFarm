"""Coupons and their redemption ledger.

A coupon is never trusted from the request beyond its code string. The
discount is always recomputed server-side from the coupon row and the
server's own line totals.
"""

from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel
from apps.core.utils import MONEY_FIELD, ZERO


class DiscountType(models.TextChoices):
    PERCENT = "percent", _("Percentage off")
    FIXED = "fixed", _("Fixed amount off")
    FREE_DELIVERY = "free_delivery", _("Free delivery")


class AppliesTo(models.TextChoices):
    ALL = "all", _("Everything")
    CATEGORIES = "categories", _("Selected categories")
    PRODUCTS = "products", _("Selected products")


class CouponQuerySet(models.QuerySet):
    def live(self):
        """Coupons that are active and inside their scheduled window."""
        now = timezone.now()
        return self.filter(is_active=True, starts_at__lte=now).filter(
            models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=now)
        )


class Coupon(TimeStampedModel):
    code = models.CharField(
        _("code"),
        max_length=32,
        unique=True,
        help_text=_("Stored uppercase. Customers type this at checkout."),
    )
    description = models.CharField(_("description"), max_length=200, blank=True)

    discount_type = models.CharField(
        _("discount type"), max_length=16, choices=DiscountType.choices, default=DiscountType.PERCENT
    )
    value = models.DecimalField(
        _("value"),
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
        help_text=_("Percent for percentage coupons, taka for fixed. Ignored for free delivery."),
        **MONEY_FIELD,
    )
    max_discount_amount = models.DecimalField(
        _("maximum discount"),
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.00"))],
        help_text=_("Caps percentage coupons. Leave empty for no cap."),
        **MONEY_FIELD,
    )
    min_order_amount = models.DecimalField(
        _("minimum order amount"),
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
        **MONEY_FIELD,
    )

    applies_to = models.CharField(
        _("applies to"), max_length=16, choices=AppliesTo.choices, default=AppliesTo.ALL
    )
    categories = models.ManyToManyField(
        "products.Category", blank=True, related_name="coupons", verbose_name=_("categories")
    )
    products = models.ManyToManyField(
        "products.Product", blank=True, related_name="coupons", verbose_name=_("products")
    )

    usage_limit_total = models.PositiveIntegerField(
        _("total usage limit"), default=0, help_text=_("0 means unlimited.")
    )
    usage_limit_per_user = models.PositiveIntegerField(
        _("per-customer limit"), default=1, help_text=_("0 means unlimited.")
    )
    times_used = models.PositiveIntegerField(_("times used"), default=0, editable=False)

    first_order_only = models.BooleanField(_("new customers only"), default=False)

    starts_at = models.DateTimeField(_("starts at"), default=timezone.now)
    expires_at = models.DateTimeField(_("expires at"), null=True, blank=True)
    is_active = models.BooleanField(_("active"), default=True, db_index=True)

    objects = CouponQuerySet.as_manager()

    class Meta:
        ordering = ("-created_at",)
        verbose_name = _("coupon")
        verbose_name_plural = _("coupons")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(value__gte=Decimal("0.00")), name="coupon_value_non_negative"
            )
        ]

    def __str__(self) -> str:
        return self.code

    def save(self, *args, **kwargs):
        self.code = self.code.strip().upper()
        super().save(*args, **kwargs)

    def clean(self):
        if self.discount_type == DiscountType.PERCENT and self.value > Decimal("100.00"):
            raise ValidationError({"value": _("A percentage discount cannot exceed 100.")})
        if self.discount_type != DiscountType.FREE_DELIVERY and self.value <= ZERO:
            raise ValidationError({"value": _("Enter a discount value above zero.")})
        if self.expires_at and self.expires_at <= self.starts_at:
            raise ValidationError({"expires_at": _("Expiry must be after the start date.")})

    # --- Status helpers -------------------------------------------------------
    @property
    def has_started(self) -> bool:
        return self.starts_at <= timezone.now()

    @property
    def is_expired(self) -> bool:
        return self.expires_at is not None and self.expires_at <= timezone.now()

    @property
    def is_exhausted(self) -> bool:
        return self.usage_limit_total > 0 and self.times_used >= self.usage_limit_total

    @property
    def is_live(self) -> bool:
        return self.is_active and self.has_started and not self.is_expired and not self.is_exhausted

    @property
    def summary(self) -> str:
        if self.discount_type == DiscountType.FREE_DELIVERY:
            return "Free delivery"
        if self.discount_type == DiscountType.PERCENT:
            return f"{self.value.normalize():f}% off"
        return f"৳{self.value:,.2f} off"


class CouponRedemption(TimeStampedModel):
    """One row per successful use. The source of truth for usage limits."""

    coupon = models.ForeignKey(
        Coupon, on_delete=models.CASCADE, related_name="redemptions", verbose_name=_("coupon")
    )
    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="coupon_redemptions",
        verbose_name=_("customer"),
    )
    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.CASCADE,
        related_name="coupon_redemptions",
        verbose_name=_("order"),
    )
    amount = models.DecimalField(_("discount amount"), default=Decimal("0.00"), **MONEY_FIELD)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = _("coupon redemption")
        verbose_name_plural = _("coupon redemptions")
        constraints = [
            models.UniqueConstraint(
                fields=["coupon", "order"], name="unique_coupon_redemption_per_order"
            )
        ]
        indexes = [models.Index(fields=["coupon", "user"])]

    def __str__(self) -> str:
        return f"{self.coupon_id} -> order {self.order_id}"
