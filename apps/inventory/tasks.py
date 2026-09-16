"""Scheduled inventory maintenance."""

from __future__ import annotations

import logging

from celery import shared_task

from apps.inventory.services import expire_batches

logger = logging.getLogger(__name__)


@shared_task(name="apps.inventory.tasks.expire_stale_batches")
def expire_stale_batches() -> int:
    """Write off expired batches so they can never be allocated to an order.

    Runs hourly. Allocation already filters on expiry date, so this is a
    belt-and-braces cleanup that also produces the write-off audit trail.
    """
    count = expire_batches()
    logger.info("expire_stale_batches wrote off %s batches", count)
    return count
