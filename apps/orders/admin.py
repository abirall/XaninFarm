"""Django admin for orders.

Order lines and money are read-only: an order is a financial record, and the
only sanctioned way to change one is through ``apps.orders.services``.
"""

from __future__ import annotations

from django.contrib import admin, messages
from django.db.models import Count
from django.utils.html import format_html

from apps.orders.models import Order, OrderEvent, OrderItem, OrderStatus
from apps.orders.services import CheckoutError, transition_order

STATUS_COLOURS = {
    OrderStatus.PENDING: "#A16207",
    OrderStatus.CONFIRMED: "#0369A1",
    OrderStatus.PREPARING: "#7C3AED",
    OrderStatus.PACKED: "#0F766E",
    OrderStatus.OUT_FOR_DELIVERY: "#C2410C",
    OrderStatus.DELIVERED: "#15803D",
    OrderStatus.CANCELLED: "#B91C1C",
    OrderStatus.RETURNED: "#57534E",
}


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    can_delete = False
    fields = ("product_name", "variant_name", "sku", "unit_price", "quantity", "line_total")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None) -> bool:
        return False


class OrderEventInline(admin.TabularInline):
    model = OrderEvent
    extra = 0
    can_delete = False
    fields = ("created_at", "status", "note", "created_by", "is_customer_visible")
    readonly_fields = fields
    ordering = ("created_at",)

    def has_add_permission(self, request, obj=None) -> bool:
        return False


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = (
        "number",
        "customer",
        "status_badge",
        "payment_status",
        "payment_method",
        "total",
        "line_count",
        "placed_at",
    )
    list_filter = (
        "status",
        "payment_status",
        "payment_method",
        "delivery_zone",
        "placed_at",
    )
    search_fields = (
        "number",
        "user__email",
        "user__full_name",
        "contact_phone",
        "ship_phone",
        "ship_recipient",
    )
    date_hierarchy = "placed_at"
    list_select_related = ("user", "delivery_zone")
    autocomplete_fields = ("user", "address", "coupon", "delivery_partner")
    inlines = [OrderItemInline, OrderEventInline]

    readonly_fields = (
        "number",
        "access_token",
        "subtotal",
        "discount_amount",
        "delivery_fee",
        "delivery_discount",
        "total",
        "amount_paid",
        "amount_refunded",
        "coupon_code",
        "ip_address",
        "placed_at",
        "confirmed_at",
        "packed_at",
        "dispatched_at",
        "delivered_at",
        "cancelled_at",
        "created_at",
        "updated_at",
    )

    fieldsets = (
        ("Order", {"fields": ("number", "user", "status", "placed_at")}),
        (
            "Payment",
            {
                "fields": ("payment_method", "payment_status", "amount_paid", "amount_refunded"),
                "description": (
                    "Payment status is independent of fulfilment status. Change it from "
                    "the Payments section, not here."
                ),
            },
        ),
        (
            "Money",
            {
                "fields": (
                    "subtotal",
                    "discount_amount",
                    "delivery_fee",
                    "delivery_discount",
                    "total",
                    "coupon",
                    "coupon_code",
                )
            },
        ),
        ("Contact", {"fields": ("contact_email", "contact_phone")}),
        (
            "Delivery address",
            {
                "fields": (
                    "address",
                    "ship_recipient",
                    "ship_phone",
                    "ship_alternate_phone",
                    "ship_address_line",
                    "ship_area",
                    "ship_district",
                    "ship_division",
                    "ship_postcode",
                    "ship_note",
                )
            },
        ),
        (
            "Fulfilment",
            {
                "fields": (
                    "delivery_zone",
                    "delivery_slot",
                    "delivery_partner",
                    "requested_delivery_date",
                    "tracking_note",
                )
            },
        ),
        ("Notes", {"fields": ("customer_note", "staff_note", "cancellation_reason")}),
        (
            "Timeline",
            {
                "fields": (
                    "confirmed_at",
                    "packed_at",
                    "dispatched_at",
                    "delivered_at",
                    "cancelled_at",
                ),
                "classes": ("collapse",),
            },
        ),
        (
            "Audit",
            {"fields": ("access_token", "ip_address", "created_at", "updated_at"), "classes": ("collapse",)},
        ),
    )

    actions = [
        "mark_confirmed",
        "mark_preparing",
        "mark_packed",
        "mark_out_for_delivery",
        "mark_delivered",
    ]

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_lines=Count("items", distinct=True))

    def has_add_permission(self, request) -> bool:
        # Orders are created by checkout, never typed in by hand.
        return False

    @admin.display(description="Customer", ordering="user__email")
    def customer(self, obj) -> str:
        return obj.user.email

    @admin.display(description="Lines", ordering="_lines")
    def line_count(self, obj) -> int:
        return obj._lines or 0

    @admin.display(description="Status", ordering="status")
    def status_badge(self, obj):
        return format_html(
            '<span style="color:{};font-weight:600">{}</span>',
            STATUS_COLOURS.get(obj.status, "#1C1B18"),
            obj.get_status_display(),
        )

    # --- Bulk status actions -------------------------------------------------
    def _bulk_transition(self, request, queryset, status: str) -> None:
        moved, failed = 0, 0
        for order in queryset:
            try:
                transition_order(order, status, by=request.user, note="Changed from admin")
            except CheckoutError:
                failed += 1
            else:
                moved += 1
        if moved:
            self.message_user(request, f"{moved} order(s) updated.", messages.SUCCESS)
        if failed:
            self.message_user(
                request,
                f"{failed} order(s) skipped - the change was not allowed from their current status.",
                messages.WARNING,
            )

    @admin.action(description="Mark as confirmed")
    def mark_confirmed(self, request, queryset):
        self._bulk_transition(request, queryset, OrderStatus.CONFIRMED)

    @admin.action(description="Mark as preparing")
    def mark_preparing(self, request, queryset):
        self._bulk_transition(request, queryset, OrderStatus.PREPARING)

    @admin.action(description="Mark as packed")
    def mark_packed(self, request, queryset):
        self._bulk_transition(request, queryset, OrderStatus.PACKED)

    @admin.action(description="Mark as out for delivery")
    def mark_out_for_delivery(self, request, queryset):
        self._bulk_transition(request, queryset, OrderStatus.OUT_FOR_DELIVERY)

    @admin.action(description="Mark as delivered")
    def mark_delivered(self, request, queryset):
        self._bulk_transition(request, queryset, OrderStatus.DELIVERED)


@admin.register(OrderEvent)
class OrderEventAdmin(admin.ModelAdmin):
    list_display = ("order", "status", "note", "created_by", "is_customer_visible", "created_at")
    list_filter = ("status", "is_customer_visible")
    search_fields = ("order__number", "note")
    list_select_related = ("order", "created_by")
    date_hierarchy = "created_at"

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False
