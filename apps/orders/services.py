"""Order placement and fulfilment.

``place_order`` is the only way an order comes into existence. It runs in a
single transaction that either produces a complete order with every line's
stock reserved, or nothing at all - there is no partial order.

Nothing the browser sends is trusted: prices, discounts, delivery fees and
stock are all recomputed here from server-side data.
"""

from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.cart.models import Cart
from apps.cart.services import clear_cart, quote_cart
from apps.coupons.services import CouponError, redeem_coupon, release_coupon
from apps.delivery.services import cod_available_for, resolve_zone_for_address
from apps.inventory.services import (
    InsufficientStockError,
    consume_reservations,
    release_reservations,
    reserve_stock,
    return_to_stock,
)
from apps.orders.models import (
    CLOSED_STATUSES,
    Order,
    OrderEvent,
    OrderItem,
    OrderStatus,
)
from apps.payments.constants import PaymentMethod, PaymentStatus

logger = logging.getLogger(__name__)


class CheckoutError(Exception):
    """Checkout cannot proceed. The message is safe to show the customer."""

    def __init__(self, message: str):
        self.message = str(message)
        super().__init__(self.message)


#: The only status changes the system will make. Anything else is a bug or an
#: attempt to skip a step, and is rejected.
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    OrderStatus.DRAFT: frozenset({OrderStatus.PENDING, OrderStatus.CANCELLED}),
    OrderStatus.PENDING: frozenset({OrderStatus.CONFIRMED, OrderStatus.CANCELLED}),
    OrderStatus.CONFIRMED: frozenset({OrderStatus.PREPARING, OrderStatus.CANCELLED}),
    OrderStatus.PREPARING: frozenset({OrderStatus.PACKED, OrderStatus.CANCELLED}),
    OrderStatus.PACKED: frozenset({OrderStatus.OUT_FOR_DELIVERY, OrderStatus.CANCELLED}),
    OrderStatus.OUT_FOR_DELIVERY: frozenset({OrderStatus.DELIVERED, OrderStatus.RETURNED}),
    OrderStatus.DELIVERED: frozenset({OrderStatus.RETURNED}),
    OrderStatus.CANCELLED: frozenset(),
    OrderStatus.RETURNED: frozenset(),
}


# ---------------------------------------------------------------------------
# Placing an order
# ---------------------------------------------------------------------------
@transaction.atomic
def place_order(
    *,
    user,
    cart,
    address,
    payment_method: str,
    delivery_slot=None,
    requested_delivery_date=None,
    customer_note: str = "",
    ip_address: str | None = None,
) -> Order:
    """Turn a cart into an order, reserving stock for every line.

    Raises ``CheckoutError`` for anything the customer can fix, and rolls the
    whole transaction back so no half-order or orphaned reservation survives.
    """
    if address.user_id != user.pk:
        # Ownership check: never let one customer ship to another's address.
        raise CheckoutError(_("That delivery address is not available."))

    if payment_method not in PaymentMethod.values:
        raise CheckoutError(_("Choose a valid payment method."))

    # Lock the cart so a double-submitted form cannot produce two orders.
    cart = Cart.objects.select_for_update().get(pk=cart.pk)

    zone = address.delivery_zone or resolve_zone_for_address(address)
    quote = quote_cart(cart, zone=zone, user=user)

    if quote.is_empty:
        raise CheckoutError(_("Your cart is empty."))
    if quote.coupon_error:
        raise CheckoutError(quote.coupon_error)
    if not quote.meets_minimum:
        raise CheckoutError(
            _("Orders in this area start at %(amount)s.")
            % {"amount": f"৳{quote.min_order_amount:,.2f}"}
        )
    if payment_method == PaymentMethod.COD and not cod_available_for(zone):
        raise CheckoutError(_("Cash on delivery is not available for this address."))
    if delivery_slot is not None and delivery_slot.zone_id not in (None, getattr(zone, "pk", None)):
        raise CheckoutError(_("That delivery slot is not available for this address."))

    snapshot = address.as_snapshot()
    order = Order.objects.create(
        user=user,
        status=OrderStatus.PENDING,
        payment_status=PaymentStatus.PENDING,
        payment_method=payment_method,
        contact_email=user.email,
        contact_phone=snapshot["phone"],
        address=address,
        ship_recipient=snapshot["recipient_name"],
        ship_phone=snapshot["phone"],
        ship_alternate_phone=snapshot["alternate_phone"],
        ship_division=snapshot["division"],
        ship_district=snapshot["district"],
        ship_area=snapshot["area"],
        ship_address_line=snapshot["address_line"],
        ship_postcode=snapshot["postcode"],
        ship_note=snapshot["delivery_note"],
        delivery_zone=zone,
        delivery_slot=delivery_slot,
        requested_delivery_date=requested_delivery_date,
        subtotal=quote.subtotal,
        discount_amount=quote.discount,
        delivery_fee=quote.delivery_fee,
        delivery_discount=quote.delivery_discount,
        total=quote.total,
        coupon=quote.coupon,
        coupon_code=quote.coupon.code if quote.coupon else "",
        customer_note=(customer_note or "").strip()[:500],
        ip_address=ip_address,
        placed_at=timezone.now(),
    )

    # Cash on delivery is committed the moment it is placed, so its hold never
    # expires. Prepaid orders keep the default hold and are swept up by
    # release_expired_reservations if the customer walks away.
    hold_expiry_kwargs = {"expires_at": None} if payment_method == PaymentMethod.COD else {}

    for line in quote.items:
        variant = line.variant
        item = OrderItem.objects.create(
            order=order,
            variant=variant,
            product_name=variant.product.name,
            variant_name=variant.name,
            sku=variant.sku,
            unit_price=line.unit_price,
            quantity=line.quantity,
            line_total=line.line_total,
        )
        try:
            reserve_stock(
                order=order,
                variant=variant,
                quantity=line.quantity,
                order_item=item,
                performed_by=user,
                **hold_expiry_kwargs,
            )
        except InsufficientStockError as exc:
            # Rolls back the order, every earlier line and every reservation.
            raise CheckoutError(exc.message) from exc

    if quote.coupon is not None:
        try:
            redeem_coupon(
                quote.coupon, order=order, user=user, amount=quote.discount + quote.delivery_discount
            )
        except CouponError as exc:
            raise CheckoutError(exc.message) from exc

    OrderEvent.objects.create(
        order=order,
        status=OrderStatus.PENDING,
        note=_("Order placed."),
        created_by=user,
    )

    clear_cart(cart)

    transaction.on_commit(lambda: _notify_order_placed(order.pk))
    return order


def _notify_order_placed(order_id: int) -> None:
    """Queue the confirmation email once the order is safely committed."""
    from apps.notifications.tasks import send_order_confirmation

    try:
        send_order_confirmation.delay(order_id)
    except Exception:  # pragma: no cover - a broker outage must not lose the order
        logger.exception("Could not queue confirmation email for order %s", order_id)


# ---------------------------------------------------------------------------
# Moving an order through fulfilment
# ---------------------------------------------------------------------------
@transaction.atomic
def transition_order(
    order: Order,
    new_status: str,
    *,
    by=None,
    note: str = "",
    is_customer_visible: bool = True,
) -> Order:
    """Move an order to a new fulfilment status and run its side effects.

    Payment status is never changed here except for the one legitimate
    hand-off: collecting cash when a COD order is delivered.
    """
    order = Order.objects.select_for_update().get(pk=order.pk)
    new_status = str(new_status)

    if new_status == order.status:
        return order
    if new_status not in ALLOWED_TRANSITIONS.get(order.status, frozenset()):
        raise CheckoutError(
            _("An order that is %(from)s cannot become %(to)s.")
            % {
                "from": order.get_status_display().lower(),
                "to": OrderStatus(new_status).label.lower(),
            }
        )

    now = timezone.now()
    updates = ["status", "updated_at"]
    order.status = new_status

    if new_status == OrderStatus.CONFIRMED:
        order.confirmed_at = now
        updates.append("confirmed_at")
    elif new_status == OrderStatus.PACKED:
        order.packed_at = now
        updates.append("packed_at")
    elif new_status == OrderStatus.OUT_FOR_DELIVERY:
        order.dispatched_at = now
        updates.append("dispatched_at")
        # The goods physically leave the farm: held stock becomes shipped stock.
        consume_reservations(order=order, performed_by=by)
    elif new_status == OrderStatus.DELIVERED:
        order.delivered_at = now
        updates.append("delivered_at")
        if order.is_cod and order.payment_status != PaymentStatus.PAID:
            _collect_cash_on_delivery(order, collected_by=by)
            updates += ["payment_status", "amount_paid"]
    elif new_status == OrderStatus.CANCELLED:
        order.cancelled_at = now
        updates.append("cancelled_at")
        release_reservations(order=order, reason=note or "Order cancelled", performed_by=by)
        release_coupon(order)
        if order.payment_status == PaymentStatus.PENDING:
            order.payment_status = PaymentStatus.CANCELLED
            updates.append("payment_status")
    elif new_status == OrderStatus.RETURNED:
        return_to_stock(order=order, performed_by=by, note=note or "Customer return")

    order.save(update_fields=list(dict.fromkeys(updates)))

    OrderEvent.objects.create(
        order=order,
        status=new_status,
        note=note or "",
        created_by=by,
        is_customer_visible=is_customer_visible,
    )

    transaction.on_commit(lambda: _notify_status_change(order.pk, new_status))
    return order


def _collect_cash_on_delivery(order: Order, *, collected_by=None) -> None:
    """Record the cash the rider took. Mutates the order in memory only."""
    from apps.payments.services import record_cod_collection

    order.amount_paid = order.total
    order.payment_status = PaymentStatus.PAID
    record_cod_collection(order, collected_by=collected_by)


def _notify_status_change(order_id: int, status: str) -> None:
    from apps.notifications.tasks import send_order_status_update

    try:
        send_order_status_update.delay(order_id, status)
    except Exception:  # pragma: no cover
        logger.exception("Could not queue status email for order %s", order_id)


def cancel_order(order: Order, *, by=None, reason: str = "") -> Order:
    """Cancel an order, returning its stock and its coupon use."""
    if order.status in CLOSED_STATUSES:
        raise CheckoutError(_("This order can no longer be cancelled."))
    order.cancellation_reason = (reason or "")[:200]
    order.save(update_fields=["cancellation_reason", "updated_at"])
    return transition_order(
        order, OrderStatus.CANCELLED, by=by, note=reason or "Order cancelled"
    )


def cancel_order_by_customer(order: Order, *, user, reason: str = "") -> Order:
    """Customer-initiated cancellation, with the ownership and stage checks."""
    if order.user_id != user.pk:
        raise CheckoutError(_("That order is not yours."))
    if not order.is_cancellable_by_customer:
        raise CheckoutError(
            _("This order is already being prepared for delivery. Call us to change it.")
        )
    return cancel_order(order, by=user, reason=reason or "Cancelled by customer")


def next_statuses(order: Order) -> list[tuple[str, str]]:
    """Status choices a staff member may move this order to, for the dashboard."""
    allowed = ALLOWED_TRANSITIONS.get(order.status, frozenset())
    return [(value, OrderStatus(value).label) for value in OrderStatus.values if value in allowed]
