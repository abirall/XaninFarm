"""Celery application for XaninFarm."""

from __future__ import annotations

import os

from celery import Celery
from celery.schedules import crontab

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

app = Celery("xaninfarm")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

# ---------------------------------------------------------------------------
# Periodic tasks
#
# django-celery-beat stores schedules in the database, but these defaults are
# registered in code so a fresh deployment is correct without manual setup.
# ---------------------------------------------------------------------------
app.conf.beat_schedule = {
    # Perishable goods: retire expired batches every hour so nothing expired
    # can ever be allocated to an order.
    "expire-inventory-batches": {
        "task": "apps.inventory.tasks.expire_stale_batches",
        "schedule": crontab(minute=5),
    },
    # Release stock held by orders that were never paid for.
    "release-abandoned-reservations": {
        "task": "apps.orders.tasks.release_expired_reservations",
        "schedule": crontab(minute="*/10"),
    },
    # Operational alerts for the farm team.
    "low-stock-alert": {
        "task": "apps.notifications.tasks.send_low_stock_digest",
        "schedule": crontab(hour=7, minute=30),
    },
    "expiry-alert": {
        "task": "apps.notifications.tasks.send_expiry_digest",
        "schedule": crontab(hour=7, minute=45),
    },
    # Roll up yesterday's sales for the analytics dashboard.
    "build-daily-sales-snapshot": {
        "task": "apps.analytics.tasks.build_daily_snapshots",
        "schedule": crontab(hour=1, minute=15),
    },
}


@app.task(bind=True, ignore_result=True)
def debug_task(self) -> str:  # pragma: no cover - operational helper
    return f"request: {self.request!r}"
