"""Email tasks.

Every one of these is fire-and-forget: they are queued with ``.delay()`` from
``transaction.on_commit`` hooks, so a mail failure can never undo an order
that has already been placed. Each swallows a missing row rather than
retrying forever - a deleted object is not a transient fault.
"""

from __future__ import annotations

import logging

from celery import shared_task
from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils.translation import gettext as _

from apps.inventory.services import expiring_batches, low_stock_variants
from apps.notifications.emails import absolute_url, send_email, staff_recipients
from apps.orders.models import Order, OrderStatus

logger = logging.getLogger(__name__)


@shared_task(name="apps.notifications.tasks.send_welcome_email")
def send_welcome_email(user_id: int) -> int:
    user = get_user_model().objects.filter(pk=user_id).first()
    if user is None or not user.email:
        return 0

    return send_email(
        subject=_("Welcome to XaninFarm"),
        template="welcome",
        context={"user": user, "shop_url": absolute_url("/shop/")},
        to=[user.email],
    )


@shared_task(name="apps.notifications.tasks.send_order_confirmation")
def send_order_confirmation(order_id: int) -> int:
    """Confirm to the customer, and put the order in front of the farm team."""
    order = (
        Order.objects.select_related("delivery_zone", "delivery_slot")
        .prefetch_related("items")
        .filter(pk=order_id)
        .first()
    )
    if order is None:
        logger.warning("Order %s vanished before its confirmation was sent", order_id)
        return 0

    context = {
        "order": order,
        "items": order.items.all(),
        "track_url": absolute_url(order.get_tracking_url()),
        "order_url": absolute_url(order.get_absolute_url()),
    }
    sent = send_email(
        subject=_("Order %(number)s confirmed") % {"number": order.number},
        template="order_confirmation",
        context=context,
        to=[order.contact_email],
    )

    send_email(
        subject=_("New order %(number)s - %(total)s")
        % {"number": order.number, "total": order.total},
        template="order_placed_ops",
        context=context,
        to=[settings.ORDER_NOTIFICATION_EMAIL],
    )
    return sent


@shared_task(name="apps.notifications.tasks.send_order_status_update")
def send_order_status_update(order_id: int, status: str) -> int:
    """Tell the customer their order moved - but only when it means something.

    Internal steps (an order being marked as 'preparing' five minutes after
    'confirmed') do not warrant an email.
    """
    notify_on = {
        OrderStatus.CONFIRMED: _("We are getting your order ready"),
        OrderStatus.PACKED: _("Your order is packed"),
        OrderStatus.OUT_FOR_DELIVERY: _("Your order is on its way"),
        OrderStatus.DELIVERED: _("Your order has been delivered"),
        OrderStatus.CANCELLED: _("Your order has been cancelled"),
        OrderStatus.RETURNED: _("Your return has been received"),
    }
    headline = notify_on.get(status)
    if headline is None:
        return 0

    order = Order.objects.prefetch_related("items").filter(pk=order_id).first()
    if order is None:
        return 0

    return send_email(
        subject=f"{headline} - {order.number}",
        template="order_status",
        context={
            "order": order,
            "items": order.items.all(),
            "headline": headline,
            "status_label": order.get_status_display(),
            "track_url": absolute_url(order.get_tracking_url()),
        },
        to=[order.contact_email],
    )


@shared_task(name="apps.notifications.tasks.send_low_stock_digest")
def send_low_stock_digest() -> int:
    """Morning list of what needs restocking."""
    variants = list(low_stock_variants()[:50])
    recipients = staff_recipients()
    if not variants or not recipients:
        return 0

    return send_email(
        subject=_("Low stock: %(count)d item(s) need attention") % {"count": len(variants)},
        template="low_stock_digest",
        context={
            "variants": variants,
            "threshold": settings.LOW_STOCK_THRESHOLD,
            "dashboard_url": absolute_url("/dashboard/inventory/"),
        },
        to=recipients,
    )


@shared_task(name="apps.notifications.tasks.send_expiry_digest")
def send_expiry_digest() -> int:
    """Morning list of batches about to go off - the perishable-goods alarm."""
    batches = list(expiring_batches()[:100])
    recipients = staff_recipients()
    if not batches or not recipients:
        return 0

    return send_email(
        subject=_("Expiring soon: %(count)d batch(es)") % {"count": len(batches)},
        template="expiry_digest",
        context={
            "batches": batches,
            "days": settings.EXPIRY_ALERT_DAYS,
            "dashboard_url": absolute_url("/dashboard/inventory/batches/"),
        },
        to=recipients,
    )
