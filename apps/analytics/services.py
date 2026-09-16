"""Reading the numbers.

Two kinds of function live here:

* ``build_snapshot`` / ``rebuild_snapshots`` write the nightly roll-up. They
  are idempotent - running them twice for the same date produces the same row -
  so a failed Celery beat run is fixed by simply running it again.
* everything else reads. Those helpers are what the staff dashboard calls, and
  they all aggregate in the database rather than in Python, because a shop with
  a year of trade must not load a year of orders to draw one chart.

Revenue here always means the order's frozen ``total``, never a live price
lookup: what the customer paid is history and must not move when a price does.
"""

from __future__ import annotations

from datetime import date as date_cls
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db.models import Count, DecimalField, F, Q, Sum, Value
from django.db.models.functions import Coalesce, TruncDate
from django.utils import timezone

from apps.analytics.models import DailySalesSnapshot
from apps.core.utils import ZERO, money
from apps.orders.models import CLOSED_STATUSES, Order, OrderItem, OrderStatus
from apps.payments.constants import PaymentStatus

#: Orders that cost us the sale. Subtracted from gross to get net revenue.
LOST_STATUSES = (OrderStatus.CANCELLED, OrderStatus.RETURNED)


def _money_sum(field: str, *, where: Q | None = None) -> Coalesce:
    """Sum a money column, returning 0.00 rather than NULL on an empty set."""
    return Coalesce(
        Sum(field, filter=where),
        Value(Decimal("0.00")),
        output_field=DecimalField(max_digits=14, decimal_places=2),
    )


def _quantity_sum(field: str = "quantity") -> Coalesce:
    return Coalesce(Sum(field), Value(0))


# ---------------------------------------------------------------------------
# Writing the roll-up
# ---------------------------------------------------------------------------


def build_snapshot(day: date_cls) -> DailySalesSnapshot:
    """Recompute - and overwrite - the snapshot for one calendar day."""
    orders = Order.objects.placed().filter(placed_at__date=day)

    totals = orders.aggregate(
        placed=Count("id"),
        delivered=Count("id", filter=Q(status=OrderStatus.DELIVERED)),
        cancelled=Count("id", filter=Q(status=OrderStatus.CANCELLED)),
        returned=Count("id", filter=Q(status=OrderStatus.RETURNED)),
        gross=_money_sum("total"),
        lost=_money_sum("total", where=Q(status__in=LOST_STATUSES)),
        discount=_money_sum("discount_amount"),
        delivery=_money_sum("delivery_fee"),
        refunded=_money_sum("amount_refunded"),
        cod=_money_sum(
            "total",
            where=Q(payment_status=PaymentStatus.PENDING) & ~Q(status__in=LOST_STATUSES),
        ),
    )
    items = OrderItem.objects.filter(order__in=orders).aggregate(sold=_quantity_sum())
    signups = get_user_model().objects.filter(date_joined__date=day, is_staff=False).count()

    snapshot, _created = DailySalesSnapshot.objects.update_or_create(
        date=day,
        defaults={
            "orders_placed": totals["placed"],
            "orders_delivered": totals["delivered"],
            "orders_cancelled": totals["cancelled"],
            "orders_returned": totals["returned"],
            "items_sold": items["sold"],
            "new_customers": signups,
            "gross_revenue": money(totals["gross"]),
            "net_revenue": money(totals["gross"] - totals["lost"]),
            "discount_given": money(totals["discount"]),
            "delivery_collected": money(totals["delivery"]),
            "refunded": money(totals["refunded"]),
            "cod_outstanding": money(totals["cod"]),
        },
    )
    return snapshot


def rebuild_snapshots(start: date_cls, end: date_cls) -> int:
    """Rebuild every day in ``[start, end]``. Returns how many days were written."""
    if end < start:
        start, end = end, start

    written = 0
    day = start
    while day <= end:
        build_snapshot(day)
        written += 1
        day += timedelta(days=1)
    return written


# ---------------------------------------------------------------------------
# Reading - live queries for the dashboard
# ---------------------------------------------------------------------------


def _sold_items(days: int):
    """Order lines from orders that survived, over the last ``days`` days."""
    start = timezone.localdate() - timedelta(days=days - 1)
    return (
        OrderItem.objects.filter(order__placed_at__date__gte=start)
        .exclude(order__status=OrderStatus.DRAFT)
        .exclude(order__status__in=LOST_STATUSES)
    )


def _period_totals(start: date_cls, end: date_cls) -> dict:
    kept = (
        Order.objects.placed()
        .filter(placed_at__date__gte=start, placed_at__date__lte=end)
        .exclude(status__in=LOST_STATUSES)
    )
    totals = kept.aggregate(orders=Count("id"), revenue=_money_sum("total"))
    items = OrderItem.objects.filter(order__in=kept).aggregate(sold=_quantity_sum())
    return {
        "orders": totals["orders"],
        "revenue": money(totals["revenue"]),
        "items": items["sold"],
    }


def _percent_change(before, after) -> float | None:
    """Signed percentage change, or None when there is no baseline to compare."""
    before, after = Decimal(before or 0), Decimal(after or 0)
    if not before:
        return None
    return round(float((after - before) / before * 100), 1)


def revenue_summary(days: int = 30) -> dict:
    """Headline trade figures for the last ``days`` days, plus the change."""
    today = timezone.localdate()
    period_start = today - timedelta(days=days - 1)

    current = _period_totals(period_start, today)
    previous = _period_totals(period_start - timedelta(days=days), period_start - timedelta(days=1))

    return {
        "days": days,
        "orders": current["orders"],
        "revenue": current["revenue"],
        "items": current["items"],
        "average_order_value": (
            money(current["revenue"] / current["orders"]) if current["orders"] else ZERO
        ),
        "revenue_change": _percent_change(previous["revenue"], current["revenue"]),
        "orders_change": _percent_change(previous["orders"], current["orders"]),
    }


def sales_series(days: int = 30) -> list[dict]:
    """One entry per day, oldest first, with empty days filled in.

    Reads live orders rather than snapshots so today's trade is included - the
    snapshot for today does not exist until tonight.
    """
    today = timezone.localdate()
    start = today - timedelta(days=days - 1)

    rows = (
        Order.objects.placed()
        .filter(placed_at__date__gte=start)
        .exclude(status__in=LOST_STATUSES)
        .annotate(day=TruncDate("placed_at"))
        .values("day")
        .annotate(orders=Count("id"), revenue=_money_sum("total"))
    )
    by_day = {row["day"]: row for row in rows}

    series = []
    for offset in range(days):
        day = start + timedelta(days=offset)
        row = by_day.get(day)
        series.append(
            {
                "date": day,
                "orders": row["orders"] if row else 0,
                "revenue": money(row["revenue"]) if row else ZERO,
            }
        )
    return series


def top_products(days: int = 30, limit: int = 8) -> list[dict]:
    """Best sellers by revenue over the period.

    Grouped by the frozen ``product_name`` on the line, not by the product FK,
    so a renamed or deactivated product still reports against what it sold as.
    """
    return list(
        _sold_items(days)
        .values("product_name")
        .annotate(
            quantity=_quantity_sum(),
            revenue=_money_sum("line_total"),
            orders=Count("order", distinct=True),
        )
        .order_by("-revenue")[:limit]
    )


def top_categories(days: int = 30, limit: int = 6) -> list[dict]:
    """Revenue split by category - which part of the farm pays the bills."""
    return list(
        _sold_items(days)
        .values(category=F("variant__product__category__name"))
        .annotate(quantity=_quantity_sum(), revenue=_money_sum("line_total"))
        .order_by("-revenue")[:limit]
    )


def payment_method_split(days: int = 30) -> list[dict]:
    start = timezone.localdate() - timedelta(days=days - 1)
    return list(
        Order.objects.placed()
        .filter(placed_at__date__gte=start)
        .exclude(status__in=LOST_STATUSES)
        .values("payment_method")
        .annotate(orders=Count("id"), revenue=_money_sum("total"))
        .order_by("-revenue")
    )


def order_status_counts() -> dict[str, int]:
    """How many orders sit at each stage. Drives the dashboard work queue."""
    rows = Order.objects.placed().values("status").annotate(total=Count("id"))
    counts = {row["status"]: row["total"] for row in rows}
    # Report every status, including the empty ones, so the dashboard layout
    # does not shuffle as the day progresses.
    return {status.value: counts.get(status.value, 0) for status in OrderStatus}


def outstanding_cash() -> Decimal:
    """Cash on delivery that has been promised but not yet collected."""
    due = (
        Order.objects.placed()
        .filter(payment_status=PaymentStatus.PENDING)
        .exclude(status__in=CLOSED_STATUSES)
        .aggregate(due=_money_sum("total"))["due"]
    )
    return money(due)


def customer_summary(days: int = 30) -> dict:
    """Counts of everyone, the newly signed up, and those who actually bought."""
    since = timezone.localdate() - timedelta(days=days - 1)
    customers = get_user_model().objects.filter(is_staff=False)

    active = (
        Order.objects.placed()
        .filter(placed_at__date__gte=since)
        .values("user")
        .distinct()
        .count()
    )
    return {
        "days": days,
        "total": customers.count(),
        "new": customers.filter(date_joined__date__gte=since).count(),
        "active": active,
    }
