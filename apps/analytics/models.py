"""Sales roll-ups.

Only one model here, and it stores nothing that cannot be recomputed from the
orders table. That is deliberate: the snapshot is a *cache*, so a bug in the
roll-up is fixed by rebuilding a date range rather than by reconciling two
sources of truth. Orders remain the only record of what was actually sold.

A snapshot exists so the dashboard can plot a year of trade without scanning
every order line, and so "revenue" has one definition instead of one per view.
"""

from __future__ import annotations

from decimal import Decimal

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel
from apps.core.utils import MONEY_FIELD, ZERO, money


class DailySalesSnapshot(TimeStampedModel):
    """One row per calendar day, rebuilt idempotently by the nightly task.

    ``gross_revenue`` counts every placed order; ``net_revenue`` subtracts the
    ones that were cancelled or returned. Both are kept because the first
    answers "how much did we sell?" and the second answers "how much did we
    keep?" - and conflating them is how sales dashboards start lying.
    """

    date = models.DateField(_("date"), unique=True)

    orders_placed = models.PositiveIntegerField(_("orders placed"), default=0)
    orders_delivered = models.PositiveIntegerField(_("orders delivered"), default=0)
    orders_cancelled = models.PositiveIntegerField(_("orders cancelled"), default=0)
    orders_returned = models.PositiveIntegerField(_("orders returned"), default=0)

    items_sold = models.PositiveIntegerField(_("items sold"), default=0)
    new_customers = models.PositiveIntegerField(_("new customers"), default=0)

    gross_revenue = models.DecimalField(
        _("gross revenue"),
        default=Decimal("0.00"),
        help_text=_("Total of every order placed on this date."),
        **MONEY_FIELD,
    )
    net_revenue = models.DecimalField(
        _("net revenue"),
        default=Decimal("0.00"),
        help_text=_("Gross revenue less cancelled and returned orders."),
        **MONEY_FIELD,
    )
    discount_given = models.DecimalField(
        _("discount given"), default=Decimal("0.00"), **MONEY_FIELD
    )
    delivery_collected = models.DecimalField(
        _("delivery fees"), default=Decimal("0.00"), **MONEY_FIELD
    )
    refunded = models.DecimalField(_("refunded"), default=Decimal("0.00"), **MONEY_FIELD)
    cod_outstanding = models.DecimalField(
        _("cash to collect"),
        default=Decimal("0.00"),
        help_text=_("Placed on this date and not yet paid for."),
        **MONEY_FIELD,
    )

    class Meta:
        ordering = ("-date",)
        verbose_name = _("daily sales snapshot")
        verbose_name_plural = _("daily sales snapshots")
        indexes = [models.Index(fields=["-date"])]

    def __str__(self) -> str:
        return f"{self.date}: {self.orders_placed} order(s)"

    @property
    def average_order_value(self) -> Decimal:
        if not self.orders_placed:
            return ZERO
        return money(self.net_revenue / self.orders_placed)

    @property
    def cancellation_rate(self) -> float:
        """Percentage of the day's orders that did not survive to delivery."""
        if not self.orders_placed:
            return 0.0
        lost = self.orders_cancelled + self.orders_returned
        return round(lost / self.orders_placed * 100, 1)
