"""Coupon validation, quoting and redemption.

The customer only ever sends a code string. Everything else - eligibility,
the discounted amount, the usage limits - is computed here from server-side
data, then re-checked under a row lock when the order is placed.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from django.db import transaction
from django.db.models import F, Q
from django.utils.translation import gettext_lazy as _

from apps.core.utils import ZERO, money, percentage_of
from apps.coupons.models import AppliesTo, Coupon, CouponRedemption, DiscountType


class CouponError(Exception):
    """A coupon cannot be applied. The message is safe to show the customer."""

    def __init__(self, message: str):
        self.message = str(message)
        super().__init__(self.message)


@dataclass(frozen=True)
class CouponQuote:
    """What a coupon is worth against a specific basket."""

    coupon: Coupon
    discount: Decimal  # taken off the goods subtotal
    delivery_discount: Decimal  # taken off the delivery fee

    @property
    def total(self) -> Decimal:
        return money(self.discount + self.delivery_discount)


def get_coupon(code: str) -> Coupon:
    """Look up a coupon by code, or raise a generic not-found error."""
    cleaned = (code or "").strip().upper()
    if not cleaned:
        raise CouponError(_("Enter a coupon code."))
    try:
        return Coupon.objects.get(code=cleaned)
    except Coupon.DoesNotExist:
        # Deliberately generic: do not confirm which codes exist.
        raise CouponError(_("That coupon code is not valid.")) from None


def eligible_subtotal(coupon: Coupon, items: Iterable) -> Decimal:
    """Sum of line totals the coupon is allowed to discount.

    Items must expose ``variant`` (a ProductVariant) and ``line_total``.
    """
    if coupon.applies_to == AppliesTo.ALL:
        return money(sum((item.line_total for item in items), ZERO))

    if coupon.applies_to == AppliesTo.PRODUCTS:
        allowed = set(coupon.products.values_list("id", flat=True))
        return money(
            sum(
                (item.line_total for item in items if item.variant.product_id in allowed),
                ZERO,
            )
        )

    category_ids = set(coupon.categories.values_list("id", flat=True))
    if not category_ids:
        return ZERO
    # Include children of the selected categories.
    from apps.products.models import Category

    expanded = set(category_ids)
    expanded.update(
        Category.objects.filter(parent_id__in=category_ids).values_list("id", flat=True)
    )
    return money(
        sum(
            (item.line_total for item in items if item.variant.product.category_id in expanded),
            ZERO,
        )
    )


def quote_coupon(
    coupon: Coupon,
    *,
    items: Iterable,
    subtotal: Decimal,
    delivery_fee: Decimal = ZERO,
) -> CouponQuote:
    """Compute what the coupon is worth. Assumes validation already passed."""
    subtotal = money(subtotal)
    delivery_fee = money(delivery_fee)
    eligible = min(eligible_subtotal(coupon, items), subtotal)

    if coupon.discount_type == DiscountType.FREE_DELIVERY:
        return CouponQuote(coupon=coupon, discount=ZERO, delivery_discount=delivery_fee)

    if coupon.discount_type == DiscountType.PERCENT:
        discount = percentage_of(eligible, coupon.value)
        if coupon.max_discount_amount is not None:
            discount = min(discount, money(coupon.max_discount_amount))
    else:
        discount = money(coupon.value)

    # Never discount more than the goods are worth.
    discount = max(ZERO, min(discount, eligible))
    return CouponQuote(coupon=coupon, discount=discount, delivery_discount=ZERO)


def validate_coupon(
    code: str,
    *,
    user=None,
    items: Iterable,
    subtotal: Decimal,
    delivery_fee: Decimal = ZERO,
) -> CouponQuote:
    """Validate a code against a basket and return its quote.

    Raises ``CouponError`` with a customer-safe message on any failure.
    """
    coupon = get_coupon(code)
    items = list(items)
    subtotal = money(subtotal)

    if not coupon.is_active or not coupon.has_started:
        raise CouponError(_("That coupon code is not valid."))
    if coupon.is_expired:
        raise CouponError(_("This coupon has expired."))
    if coupon.is_exhausted:
        raise CouponError(_("This coupon has been fully claimed."))
    if subtotal < coupon.min_order_amount:
        raise CouponError(
            _("Spend %(amount)s to use this coupon.")
            % {"amount": f"৳{coupon.min_order_amount:,.2f}"}
        )

    authenticated = user is not None and getattr(user, "is_authenticated", False)

    if coupon.first_order_only:
        if not authenticated:
            raise CouponError(_("Sign in to use this coupon."))
        if _has_previous_order(user):
            raise CouponError(_("This coupon is for first orders only."))

    if authenticated and coupon.usage_limit_per_user > 0:
        used = CouponRedemption.objects.filter(coupon=coupon, user=user).count()
        if used >= coupon.usage_limit_per_user:
            raise CouponError(_("You have already used this coupon."))

    quote = quote_coupon(
        coupon, items=items, subtotal=subtotal, delivery_fee=delivery_fee
    )
    if quote.total <= ZERO:
        if coupon.discount_type == DiscountType.FREE_DELIVERY:
            raise CouponError(_("This order already qualifies for free delivery."))
        if coupon.applies_to != AppliesTo.ALL:
            raise CouponError(_("This coupon does not apply to the items in your cart."))
        raise CouponError(_("This coupon has no value on this order."))
    return quote


def _has_previous_order(user) -> bool:
    from apps.orders.models import Order, OrderStatus

    return (
        Order.objects.filter(user=user)
        .exclude(status__in=[OrderStatus.CANCELLED, OrderStatus.DRAFT])
        .exists()
    )


@transaction.atomic
def redeem_coupon(coupon: Coupon, *, order, user=None, amount: Decimal) -> CouponRedemption:
    """Record a redemption, re-checking limits under a row lock.

    Idempotent per order: calling twice for the same order returns the
    existing redemption without double-counting.
    """
    locked = Coupon.objects.select_for_update().get(pk=coupon.pk)

    existing = CouponRedemption.objects.filter(coupon=locked, order=order).first()
    if existing is not None:
        return existing

    if locked.usage_limit_total > 0 and locked.times_used >= locked.usage_limit_total:
        raise CouponError(_("This coupon has been fully claimed."))

    if user is not None and getattr(user, "is_authenticated", False):
        if locked.usage_limit_per_user > 0:
            used = CouponRedemption.objects.filter(coupon=locked, user=user).count()
            if used >= locked.usage_limit_per_user:
                raise CouponError(_("You have already used this coupon."))
    else:
        user = None

    redemption = CouponRedemption.objects.create(
        coupon=locked, user=user, order=order, amount=money(amount)
    )
    Coupon.objects.filter(pk=locked.pk).update(times_used=F("times_used") + 1)
    return redemption


@transaction.atomic
def release_coupon(order) -> None:
    """Give the coupon use back when an order is cancelled."""
    redemptions = list(
        CouponRedemption.objects.select_related("coupon").filter(order=order)
    )
    for redemption in redemptions:
        Coupon.objects.filter(pk=redemption.coupon_id, times_used__gt=0).update(
            times_used=F("times_used") - 1
        )
    CouponRedemption.objects.filter(order=order).delete()


def live_public_coupons():
    """Coupons safe to advertise on the storefront."""
    return Coupon.objects.live().filter(
        Q(usage_limit_total=0) | Q(times_used__lt=F("usage_limit_total"))
    )
