"""Nightly sales roll-up.

Yesterday is rebuilt rather than merely created, so a run that is retried,
delayed past midnight, or dispatched twice leaves exactly one correct row.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from apps.analytics.services import rebuild_snapshots

logger = logging.getLogger(__name__)


@shared_task(name="apps.analytics.tasks.build_daily_snapshots")
def build_daily_snapshots(days: int = 2) -> int:
    """Roll up the last ``days`` days.

    Two days by default, not one: an order placed at 23:58 and confirmed at
    00:03 would otherwise leave yesterday's figures stale, and rebuilding a
    day is cheap.
    """
    today = timezone.localdate()
    written = rebuild_snapshots(today - timedelta(days=max(days - 1, 0)), today)
    logger.info("Rebuilt %s daily sales snapshot(s)", written)
    return written


@shared_task(name="apps.analytics.tasks.backfill_snapshots")
def backfill_snapshots(days: int = 90) -> int:
    """One-off helper for a fresh install, or after fixing a reporting bug."""
    today = timezone.localdate()
    return rebuild_snapshots(today - timedelta(days=days), today)
