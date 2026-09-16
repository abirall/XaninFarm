"""Django admin for payments.

Payments are evidence, not data entry: nothing here can invent one. Staff may
read the ledger and issue a refund through the gateway, and that is all.
"""

from __future__ import annotations

from django.contrib import admin, messages
from django.utils.html import format_html

from apps.payments.constants import PaymentStatus
from apps.payments.models import Payment, Refund, WebhookEvent
from apps.payments.providers import PaymentError
from apps.payments.services import refund_payment, verify_payment

STATUS_COLOURS = {
    PaymentStatus.PENDING: "#A16207",
    PaymentStatus.AUTHORIZED: "#0369A1",
    PaymentStatus.PAID: "#15803D",
    PaymentStatus.PARTIALLY_REFUNDED: "#C2410C",
    PaymentStatus.REFUNDED: "#57534E",
    PaymentStatus.FAILED: "#B91C1C",
    PaymentStatus.CANCELLED: "#78716C",
}


def status_badge(status: str, label: str):
    return format_html(
        '<span style="background:{};color:#fff;padding:2px 8px;'
        'border-radius:9999px;font-size:11px;">{}</span>',
        STATUS_COLOURS.get(status, "#57534E"),
        label,
    )


class RefundInline(admin.TabularInline):
    model = Refund
    extra = 0
    can_delete = False
    fields = ("created_at", "amount", "status", "reason", "provider_reference", "created_by")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None) -> bool:
        return False


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = (
        "reference",
        "order_link",
        "provider",
        "amount",
        "state",
        "instrument_hint",
        "created_at",
    )
    list_filter = ("provider", "status", "created_at")
    search_fields = ("reference", "provider_reference", "order__number", "order__contact_email")
    date_hierarchy = "created_at"
    inlines = (RefundInline,)
    actions = ("reverify", "refund_in_full")
    readonly_fields = (
        "order",
        "provider",
        "reference",
        "provider_reference",
        "instrument_hint",
        "amount",
        "currency",
        "status",
        "failure_reason",
        "raw_response",
        "initiated_at",
        "paid_at",
        "failed_at",
        "recorded_by",
        "created_at",
        "updated_at",
    )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("order", "recorded_by")

    def has_add_permission(self, request) -> bool:
        # Payments are recorded by the checkout and webhook flows, never typed in.
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False

    @admin.display(description="Order", ordering="order__number")
    def order_link(self, obj: Payment) -> str:
        return obj.order.number

    @admin.display(description="Status", ordering="status")
    def state(self, obj: Payment):
        return status_badge(obj.status, obj.get_status_display())

    @admin.action(description="Re-check with the provider")
    def reverify(self, request, queryset):
        checked = 0
        for payment in queryset:
            try:
                verify_payment(payment, by=request.user)
            except PaymentError as exc:
                self.message_user(request, f"{payment.reference}: {exc}", messages.WARNING)
            else:
                checked += 1
        if checked:
            self.message_user(request, f"Re-checked {checked} payment(s).", messages.SUCCESS)

    @admin.action(description="Refund the full remaining amount")
    def refund_in_full(self, request, queryset):
        refunded = 0
        for payment in queryset:
            remaining = payment.refundable_amount
            if remaining <= 0:
                continue
            try:
                refund_payment(
                    payment, amount=remaining, reason="Refunded by staff", by=request.user
                )
            except PaymentError as exc:
                self.message_user(request, f"{payment.reference}: {exc}", messages.WARNING)
            else:
                refunded += 1
        if refunded:
            self.message_user(request, f"Refunded {refunded} payment(s).", messages.SUCCESS)


@admin.register(Refund)
class RefundAdmin(admin.ModelAdmin):
    list_display = ("created_at", "payment", "amount", "status", "created_by")
    list_filter = ("status", "created_at")
    search_fields = ("payment__reference", "provider_reference", "payment__order__number")
    readonly_fields = tuple(
        field.name for field in Refund._meta.fields if field.name != "id"
    )

    def has_add_permission(self, request) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False


@admin.register(WebhookEvent)
class WebhookEventAdmin(admin.ModelAdmin):
    """The audit trail for everything a gateway has ever told us."""

    list_display = (
        "created_at",
        "provider",
        "event_id",
        "status",
        "signature_valid",
        "payment",
        "source_ip",
    )
    list_filter = ("provider", "status", "signature_valid", "created_at")
    search_fields = ("event_id", "payment__reference", "error")
    date_hierarchy = "created_at"
    readonly_fields = tuple(
        field.name for field in WebhookEvent._meta.fields if field.name != "id"
    )

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False
