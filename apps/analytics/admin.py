"""Django admin for the sales roll-up.

Snapshots are derived data: they are rebuilt from orders, never typed in. So
the admin is read-only apart from one action - rebuild - which recomputes the
selected dates from the orders table.
"""

from __future__ import annotations

from django.contrib import admin, messages
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext

from apps.analytics.models import DailySalesSnapshot
from apps.analytics.services import build_snapshot


@admin.register(DailySalesSnapshot)
class DailySalesSnapshotAdmin(admin.ModelAdmin):
    list_display = (
        "date",
        "orders_placed",
        "orders_delivered",
        "items_sold",
        "gross_revenue",
        "net_revenue",
        "average_order_value",
        "cod_outstanding",
        "new_customers",
    )
    list_filter = ("date",)
    date_hierarchy = "date"
    ordering = ("-date",)
    actions = ("rebuild",)

    fieldsets = (
        (None, {"fields": ("date",)}),
        (
            _("Orders"),
            {
                "fields": (
                    "orders_placed",
                    "orders_delivered",
                    "orders_cancelled",
                    "orders_returned",
                    "items_sold",
                    "cancellation_rate",
                )
            },
        ),
        (
            _("Money"),
            {
                "fields": (
                    "gross_revenue",
                    "net_revenue",
                    "average_order_value",
                    "discount_given",
                    "delivery_collected",
                    "refunded",
                    "cod_outstanding",
                )
            },
        ),
        (_("Customers"), {"fields": ("new_customers",)}),
        (_("Audit"), {"fields": ("created_at", "updated_at")}),
    )

    def get_readonly_fields(self, request, obj=None):
        return [
            field.name for field in self.model._meta.fields
        ] + ["average_order_value", "cancellation_rate"]

    def has_add_permission(self, request) -> bool:
        # Snapshots are built by apps.analytics.tasks, not by hand.
        return False

    @admin.display(description=_("Avg order"))
    def average_order_value(self, obj: DailySalesSnapshot):
        return obj.average_order_value

    @admin.display(description=_("Lost %"))
    def cancellation_rate(self, obj: DailySalesSnapshot):
        return f"{obj.cancellation_rate}%"

    @admin.action(description=_("Rebuild the selected dates from orders"))
    def rebuild(self, request, queryset):
        dates = list(queryset.values_list("date", flat=True))
        for day in dates:
            build_snapshot(day)
        self.message_user(
            request,
            ngettext("Rebuilt %d date.", "Rebuilt %d dates.", len(dates)) % len(dates),
            messages.SUCCESS,
        )
