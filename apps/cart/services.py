"""Cart operations and the single source of truth for order pricing.

``quote_cart`` is deliberately the only place that turns a basket into money.
Checkout and the order writer both call it, so the customer can never be
charged a number the server did not compute.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils.translation import gettext_lazy as _

from apps.cart.models import Cart, CartItem, Wishlist, WishlistItem
from apps.core.utils import ZERO, money
from apps.coupons.services import CouponError, validate_coupon
from apps.delivery.services import (
    cod_available_for,
    delivery_fee_for,
    free_delivery_threshold_for,
    min_order_amount_for,
)
from apps.inventory.services import available_quantity, available_quantity_map

MAX_QUANTITY = getattr(settings, "CART_MAX_QUANTITY_PER_ITEM", 50)

#: Django cycles the session key during login, so the guest cart is tracked by
#: id in the session data (which survives the cycle) rather than by key alone.
SESSION_CART_KEY = "cart_id"


class CartError(Exception):
    """A cart operation failed. The message is safe to show the customer."""

    def __init__(self, message: str):
        self.message = str(message)
        super().__init__(self.message)


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class StockIssue:
    item: CartItem
    available: Decimal

    @property
    def is_out_of_stock(self) -> bool:
        return self.available <= 0

    @property
    def message(self) -> str:
        name = f"{self.item.variant.product.name} ({self.item.variant.name})"
        if self.is_out_of_stock:
            return _("%(name)s is out of stock.") % {"name": name}
        return _("Only %(count)s left of %(name)s.") % {
            "count": int(self.available),
            "name": name,
        }


@dataclass(frozen=True)
class CartQuote:
    """Every number the checkout page and the order writer need."""

    items: list[CartItem]
    subtotal: Decimal
    discount: Decimal
    delivery_fee: Decimal
    delivery_discount: Decimal
    total: Decimal
    savings: Decimal
    coupon: object | None = None
    coupon_error: str = ""
    zone: object | None = None
    min_order_amount: Decimal = ZERO
    free_delivery_threshold: Decimal = ZERO
    stock_issues: list[StockIssue] = field(default_factory=list)

    @property
    def total_quantity(self) -> int:
        return sum(item.quantity for item in self.items)

    @property
    def is_empty(self) -> bool:
        return not self.items

    @property
    def goods_total(self) -> Decimal:
        return money(self.subtotal - self.discount)

    @property
    def meets_minimum(self) -> bool:
        return self.subtotal >= self.min_order_amount

    @property
    def amount_to_free_delivery(self) -> Decimal:
        if self.free_delivery_threshold <= ZERO or self.delivery_fee <= ZERO:
            return ZERO
        return max(ZERO, money(self.free_delivery_threshold - self.goods_total))

    @property
    def has_free_delivery(self) -> bool:
        return self.delivery_fee <= ZERO or self.delivery_discount >= self.delivery_fee

    @property
    def is_checkoutable(self) -> bool:
        return bool(self.items) and self.meets_minimum and not self.stock_issues

    @property
    def cod_available(self) -> bool:
        return cod_available_for(self.zone)


def quote_cart(cart: Cart | None, *, zone=None, user=None, check_stock: bool = False) -> CartQuote:
    """Price a cart. The only function allowed to decide what an order costs."""
    items = list(cart.line_items()) if cart is not None else []
    subtotal = money(sum((item.line_total for item in items), ZERO))
    savings = money(sum((item.line_savings for item in items), ZERO))

    threshold = free_delivery_threshold_for(zone)
    minimum = min_order_amount_for(zone)

    discount = ZERO
    delivery_discount = ZERO
    coupon = None
    coupon_error = ""

    code = (cart.coupon_code if cart is not None else "") or ""
    base_delivery_fee = delivery_fee_for(zone, subtotal)

    if code and items:
        try:
            quote = validate_coupon(
                code,
                user=user,
                items=items,
                subtotal=subtotal,
                delivery_fee=base_delivery_fee,
            )
        except CouponError as exc:
            coupon_error = exc.message
        else:
            coupon = quote.coupon
            discount = quote.discount
            delivery_discount = quote.delivery_discount

    # The free-delivery threshold is judged on what the customer actually pays
    # for goods, so a discount can push an order below it.
    delivery_fee = delivery_fee_for(zone, money(subtotal - discount))
    delivery_discount = min(delivery_discount, delivery_fee)

    total = money(subtotal - discount + delivery_fee - delivery_discount)

    stock_issues: list[StockIssue] = []
    if check_stock and items:
        stock_map = available_quantity_map([item.variant for item in items])
        for item in items:
            available = stock_map.get(item.variant_id, ZERO)
            if available < item.quantity:
                stock_issues.append(StockIssue(item=item, available=available))

    return CartQuote(
        items=items,
        subtotal=subtotal,
        discount=discount,
        delivery_fee=delivery_fee,
        delivery_discount=delivery_discount,
        total=max(ZERO, total),
        savings=savings,
        coupon=coupon,
        coupon_error=coupon_error,
        zone=zone,
        min_order_amount=minimum,
        free_delivery_threshold=threshold,
        stock_issues=stock_issues,
    )


# ---------------------------------------------------------------------------
# Cart resolution
# ---------------------------------------------------------------------------
def get_cart(request, *, create: bool = True) -> Cart | None:
    """Return the caller's cart, creating one only when asked."""
    if request.user.is_authenticated:
        if create:
            cart, _created = Cart.objects.get_or_create(user=request.user)
            return cart
        return Cart.objects.filter(user=request.user).first()

    session_key = request.session.session_key
    if not session_key:
        if not create:
            return None
        request.session.save()
        session_key = request.session.session_key

    if create:
        cart, _created = Cart.objects.get_or_create(user=None, session_key=session_key)
        request.session[SESSION_CART_KEY] = cart.pk
        return cart
    return Cart.objects.filter(user=None, session_key=session_key).first()


@transaction.atomic
def merge_carts(*, session_cart: Cart, user) -> Cart:
    """Fold an anonymous cart into the customer's cart at login.

    Quantities are summed and clamped; the guest coupon code wins only if the
    customer had none.
    """
    user_cart, _created = Cart.objects.get_or_create(user=user)
    if session_cart.pk == user_cart.pk:
        return user_cart

    existing = {item.variant_id: item for item in user_cart.items.all()}
    for item in session_cart.items.all():
        target = existing.get(item.variant_id)
        if target is None:
            item.cart = user_cart
            item.quantity = min(item.quantity, MAX_QUANTITY)
            item.save(update_fields=["cart", "quantity", "updated_at"])
        else:
            target.quantity = min(target.quantity + item.quantity, MAX_QUANTITY)
            target.save(update_fields=["quantity", "updated_at"])

    if session_cart.coupon_code and not user_cart.coupon_code:
        user_cart.coupon_code = session_cart.coupon_code
        user_cart.save(update_fields=["coupon_code", "updated_at"])

    session_cart.delete()
    return user_cart


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------
def _assert_sellable(variant, quantity: int) -> None:
    if not variant.is_active or not variant.product.is_active:
        raise CartError(_("That item is no longer available."))
    available = available_quantity(variant)
    if available <= 0:
        raise CartError(_("%(name)s is out of stock.") % {"name": variant.product.name})
    if available < quantity:
        raise CartError(
            _("Only %(count)s left of %(name)s.")
            % {"count": int(available), "name": variant.product.name}
        )


@transaction.atomic
def add_item(cart: Cart, variant, quantity: int = 1) -> CartItem:
    """Add to the cart, merging with any existing line for the same variant."""
    quantity = _clean_quantity(quantity)
    item = cart.items.select_for_update().filter(variant=variant).first()
    wanted = min((item.quantity if item else 0) + quantity, MAX_QUANTITY)

    _assert_sellable(variant, wanted)

    if item is None:
        item = CartItem.objects.create(cart=cart, variant=variant, quantity=wanted)
    else:
        item.quantity = wanted
        item.save(update_fields=["quantity", "updated_at"])

    _touch(cart)
    return item


@transaction.atomic
def set_quantity(cart: Cart, item: CartItem, quantity: int) -> CartItem | None:
    """Set an exact line quantity. Zero removes the line."""
    if item.cart_id != cart.pk:
        raise CartError(_("That item is not in your cart."))

    if quantity <= 0:
        item.delete()
        _touch(cart)
        return None

    quantity = _clean_quantity(quantity)
    _assert_sellable(item.variant, quantity)
    item.quantity = quantity
    item.save(update_fields=["quantity", "updated_at"])
    _touch(cart)
    return item


def remove_item(cart: Cart, item: CartItem) -> None:
    if item.cart_id != cart.pk:
        raise CartError(_("That item is not in your cart."))
    item.delete()
    _touch(cart)


def clear_cart(cart: Cart) -> None:
    cart.items.all().delete()
    if cart.coupon_code:
        cart.coupon_code = ""
        cart.save(update_fields=["coupon_code", "updated_at"])
    else:
        _touch(cart)


def apply_coupon(cart: Cart, code: str, *, user=None, zone=None) -> CartQuote:
    """Attach a coupon after validating it. Raises CouponError when invalid."""
    items = list(cart.line_items())
    if not items:
        raise CouponError(_("Add something to your cart first."))

    subtotal = money(sum((item.line_total for item in items), ZERO))
    validate_coupon(
        code,
        user=user,
        items=items,
        subtotal=subtotal,
        delivery_fee=delivery_fee_for(zone, subtotal),
    )
    cart.coupon_code = (code or "").strip().upper()
    cart.save(update_fields=["coupon_code", "updated_at"])
    return quote_cart(cart, zone=zone, user=user)


def remove_coupon(cart: Cart) -> None:
    if cart.coupon_code:
        cart.coupon_code = ""
        cart.save(update_fields=["coupon_code", "updated_at"])


def _clean_quantity(quantity) -> int:
    try:
        value = int(quantity)
    except (TypeError, ValueError):
        raise CartError(_("Enter a valid quantity.")) from None
    if value < 1:
        raise CartError(_("Enter a valid quantity."))
    return min(value, MAX_QUANTITY)


def _touch(cart: Cart) -> None:
    """Bump updated_at (auto_now) so abandoned-cart reporting stays honest."""
    cart.save(update_fields=["updated_at"])


# ---------------------------------------------------------------------------
# Wishlist
# ---------------------------------------------------------------------------
def toggle_wishlist(user, product) -> bool:
    """Add or remove a product. Returns True when it ends up saved."""
    wishlist, _created = Wishlist.objects.get_or_create(user=user)
    item = WishlistItem.objects.filter(wishlist=wishlist, product=product).first()
    if item is not None:
        item.delete()
        return False
    WishlistItem.objects.create(wishlist=wishlist, product=product)
    return True


def wishlisted_product_ids(user) -> set[int]:
    if not user.is_authenticated:
        return set()
    return set(
        WishlistItem.objects.filter(wishlist__user=user).values_list("product_id", flat=True)
    )
