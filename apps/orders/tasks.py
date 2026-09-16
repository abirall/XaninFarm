"""Scheduled order maintenance."""

from __future__ import annotations

import logging

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from apps.inventory.models import StockReservation
from apps.orders.models import Order, OrderStatus
from apps.payments.constants import PaymentStatus

logger = logging.getLogger(__name__)


@shared_task(name="apps.orders.tasks.release_expired_reservations")
def release_expired_reservations() -> int:
    """Cancel unpaid orders whose stock hold has timed out.

    Prepaid checkouts hold stock for ``STOCK_RESERVATION_MINUTES``. If the
    customer never completes payment, the goods must go back on the shelf
    rather than sit reserved for someone who has walked away.

    Cash-on-delivery orders are placed with no expiry, so they are never
    swept up here.
    """
    from apps.orders.services import cancel_order

    now = timezone.now()
    order_ids = (
        StockReservation.objects.filter(
            status=StockReservation.Status.HELD,
            expires_at__isnull=False,
            expires_at__lte=now,
        )
        .values_list("order_id", flat=True)
        .distinct()
    )

    cancelled = 0
    for order_id in list(order_ids):
        with transaction.atomic():
            order = Order.objects.select_for_update().filter(pk=order_id).first()
            if order is None:
                continue

            unpaid = order.payment_status in {PaymentStatus.PENDING, PaymentStatus.FAILED}
            if order.status == OrderStatus.PENDING and unpaid:
                cancel_order(order, reason="Payment not completed in time")
                cancelled += 1
                continue

            # The order moved on (paid, or confirmed by staff). Clear the
            # timeout so the sweep does not keep re-examining it.
            StockReservation.objects.filter(
                order=order, status=StockReservation.Status.HELD
            ).update(expires_at=None)

    if cancelled:
        logger.info("Released stock from %s abandoned order(s).", cancelled)
    return cancelled
