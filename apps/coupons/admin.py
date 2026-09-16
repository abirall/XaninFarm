"""Django admin for coupons."""

from __future__ import annotations

from django.contrib import admin
from django.utils.html import format_html

from apps.coupons.models import Coupon, CouponRedemption


class CouponRedemptionInline(admin.TabularInline):
    model = CouponRedemption
    extra = 0
    can_delete = False
    fields = ("order", "user", "amount", "created_at")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None) -> bool:
        return False


@admin.register(Coupon)
class CouponAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "summary",
        "applies_to",
        "min_order_amount",
        "usage_display",
        "starts_at",
        "expires_at",
        "status",
        "is_active",
    )
    list_editable = ("is_active",)
    list_filter = ("is_active", "discount_type", "applies_to", "first_order_only")
    search_fields = ("code", "description")
    readonly_fields = ("times_used", "created_at", "updated_at")
    filter_horizontal = ("categories", "products")
    date_hierarchy = "starts_at"
    inlines = [CouponRedemptionInline]

    fieldsets = (
        ("Coupon", {"fields": ("code", "description", "is_active")}),
        (
            "Discount",
            {"fields": ("discount_type", "value", "max_discount_amount", "min_order_amount")},
        ),
        ("Scope", {"fields": ("applies_to", "categories", "products")}),
        (
            "Limits",
            {"fields": ("usage_limit_total", "usage_limit_per_user", "first_order_only", "times_used")},
        ),
        ("Schedule", {"fields": ("starts_at", "expires_at")}),
        ("Audit", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )

    @admin.display(description="Discount")
    def summary(self, obj) -> str:
        return obj.summary

    @admin.display(description="Used")
    def usage_display(self, obj) -> str:
        if obj.usage_limit_total:
            return f"{obj.times_used} / {obj.usage_limit_total}"
        return f"{obj.times_used} / ∞"

    @admin.display(description="Status")
    def status(self, obj):
        if obj.is_live:
            return format_html('<span style="color:#15803D;font-weight:600">Live</span>')
        if obj.is_expired:
            label = "Expired"
        elif obj.is_exhausted:
            label = "Claimed out"
        elif not obj.has_started:
            label = "Scheduled"
        else:
            label = "Paused"
        return format_html('<span style="color:#A16207;font-weight:600">{}</span>', label)


@admin.register(CouponRedemption)
class CouponRedemptionAdmin(admin.ModelAdmin):
    list_display = ("coupon", "order", "user", "amount", "created_at")
    list_filter = ("coupon",)
    search_fields = ("coupon__code", "order__number", "user__email")
    list_select_related = ("coupon", "order", "user")
    date_hierarchy = "created_at"

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False
