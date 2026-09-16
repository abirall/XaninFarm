"""Django admin for stock batches, movements and reservations."""

from __future__ import annotations

from django.contrib import admin, messages
from django.utils.html import format_html
from django.utils.translation import ngettext

from apps.inventory.models import BatchStatus, InventoryBatch, StockMovement, StockReservation


class ExpiryStateFilter(admin.SimpleListFilter):
    title = "expiry"
    parameter_name = "expiry_state"

    def lookups(self, request, model_admin):
        return [
            ("expired", "Expired"),
            ("critical", "Expires within 1 day"),
            ("soon", "Expires within 7 days"),
            ("none", "No expiry date"),
        ]

    def queryset(self, request, queryset):
        value = self.value()
        if value == "expired":
            return queryset.expired()
        if value == "critical":
            return queryset.expiring_within(1)
        if value == "soon":
            return queryset.expiring_within(7)
        if value == "none":
            return queryset.filter(expiry_date__isnull=True)
        return queryset


class StockMovementInline(admin.TabularInline):
    model = StockMovement
    extra = 0
    can_delete = False
    max_num = 0  # history is append-only; add rows through the services layer
    fields = (
        "created_at",
        "movement_type",
        "quantity",
        "available_delta",
        "reserved_delta",
        "balance_available",
        "balance_reserved",
        "reference_type",
        "reference_id",
        "performed_by",
        "note",
    )
    readonly_fields = fields
    ordering = ("-created_at",)


@admin.register(InventoryBatch)
class InventoryBatchAdmin(admin.ModelAdmin):
    list_display = (
        "batch_number",
        "variant",
        "status",
        "expiry_badge",
        "quantity_available",
        "quantity_reserved",
        "quantity_received",
        "received_date",
    )
    list_filter = ("status", ExpiryStateFilter, "variant__product__category")
    search_fields = ("batch_number", "variant__sku", "variant__product__name", "supplier")
    autocomplete_fields = ("variant",)
    date_hierarchy = "received_date"
    readonly_fields = ("created_at", "updated_at")
    inlines = [StockMovementInline]
    actions = ["mark_quarantine", "mark_recalled"]

    fieldsets = (
        (
            "Batch",
            {"fields": ("variant", "batch_number", "status", "supplier", "storage_location")},
        ),
        ("Dates", {"fields": ("production_date", "received_date", "expiry_date")}),
        (
            "Quantities",
            {
                "fields": ("quantity_received", "quantity_available", "quantity_reserved"),
                "description": (
                    "Edit with care. Prefer the dashboard's adjust action, which writes "
                    "an audited stock movement."
                ),
            },
        ),
        ("Cost & notes", {"fields": ("unit_cost", "notes")}),
        ("Audit", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )

    @admin.display(description="Expiry", ordering="expiry_date")
    def expiry_badge(self, obj):
        if not obj.expiry_date:
            return format_html('<span style="color:#8A877D">-</span>')
        colours = {
            "expired": "#B91C1C",
            "critical": "#C2410C",
            "soon": "#A16207",
            "fresh": "#15803D",
        }
        colour = colours.get(obj.expiry_state, "#57544D")
        days = obj.days_to_expiry
        suffix = "expired" if days is not None and days < 0 else f"{days}d left"
        return format_html(
            '<span style="color:{};font-weight:600">{} ({})</span>', colour, obj.expiry_date, suffix
        )

    @admin.action(description="Move selected batches to quarantine")
    def mark_quarantine(self, request, queryset):
        updated = queryset.update(status=BatchStatus.QUARANTINE)
        self.message_user(
            request,
            ngettext("%d batch quarantined.", "%d batches quarantined.", updated) % updated,
            messages.WARNING,
        )

    @admin.action(description="Mark selected batches as recalled")
    def mark_recalled(self, request, queryset):
        updated = queryset.update(status=BatchStatus.RECALLED)
        self.message_user(
            request,
            ngettext("%d batch recalled.", "%d batches recalled.", updated) % updated,
            messages.WARNING,
        )


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "variant",
        "movement_type",
        "quantity",
        "available_delta",
        "reserved_delta",
        "reference_type",
        "reference_id",
        "performed_by",
    )
    list_filter = ("movement_type", "created_at")
    search_fields = ("variant__sku", "batch__batch_number", "reference_id", "note")
    date_hierarchy = "created_at"
    list_select_related = ("variant", "batch", "performed_by")

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False  # append-only audit log

    def has_delete_permission(self, request, obj=None) -> bool:
        return False


@admin.register(StockReservation)
class StockReservationAdmin(admin.ModelAdmin):
    list_display = (
        "order",
        "variant",
        "batch",
        "quantity",
        "status",
        "expires_at",
        "resolved_at",
    )
    list_filter = ("status",)
    search_fields = ("order__number", "variant__sku", "batch__batch_number")
    list_select_related = ("order", "variant", "batch")
    readonly_fields = ("resolved_at",)

    def has_add_permission(self, request) -> bool:
        return False
