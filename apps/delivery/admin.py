"""Django admin for delivery zones, slots and riders."""

from __future__ import annotations

from django.contrib import admin

from apps.delivery.models import DeliveryPartner, DeliverySlot, DeliveryZone


class DeliverySlotInline(admin.TabularInline):
    model = DeliverySlot
    extra = 0
    fields = ("label", "start_time", "end_time", "cutoff_hours", "max_orders_per_day", "is_active")


@admin.register(DeliveryZone)
class DeliveryZoneAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "delivery_fee",
        "free_delivery_threshold",
        "min_order_amount",
        "estimate_label",
        "cod_available",
        "is_default",
        "is_active",
    )
    list_editable = ("delivery_fee", "cod_available", "is_active")
    list_filter = ("is_active", "cod_available", "is_default")
    search_fields = ("name", "districts", "areas", "postcodes")
    prepopulated_fields = {"slug": ("name",)}
    inlines = [DeliverySlotInline]

    fieldsets = (
        ("Zone", {"fields": ("name", "slug", "description", "sort_order")}),
        (
            "Coverage",
            {
                "fields": ("postcodes", "areas", "districts"),
                "description": (
                    "Comma separated lists. An address is matched by postcode first, "
                    "then area, then district."
                ),
            },
        ),
        (
            "Pricing",
            {"fields": ("delivery_fee", "free_delivery_threshold", "min_order_amount")},
        ),
        ("Timing", {"fields": ("estimated_days_min", "estimated_days_max")}),
        ("Availability", {"fields": ("cod_available", "is_active", "is_default")}),
    )

    @admin.display(description="Estimate")
    def estimate_label(self, obj) -> str:
        return obj.estimate_label


@admin.register(DeliverySlot)
class DeliverySlotAdmin(admin.ModelAdmin):
    list_display = (
        "label",
        "zone",
        "start_time",
        "end_time",
        "cutoff_hours",
        "max_orders_per_day",
        "is_active",
    )
    list_filter = ("is_active", "zone")
    search_fields = ("label",)
    list_select_related = ("zone",)


@admin.register(DeliveryPartner)
class DeliveryPartnerAdmin(admin.ModelAdmin):
    list_display = ("name", "phone", "vehicle", "zone_list", "is_active")
    list_filter = ("is_active", "vehicle", "zones")
    search_fields = ("name", "phone")
    filter_horizontal = ("zones",)
    autocomplete_fields = ("user",)

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("zones")

    @admin.display(description="Zones")
    def zone_list(self, obj) -> str:
        return ", ".join(zone.name for zone in obj.zones.all()) or "-"
