"""Django admin for carts and wishlists (read-mostly - carts are transient)."""

from __future__ import annotations

from django.contrib import admin
from django.db.models import Count, Sum

from apps.cart.models import Cart, CartItem, Wishlist, WishlistItem


class CartItemInline(admin.TabularInline):
    model = CartItem
    extra = 0
    fields = ("variant", "quantity", "unit_price", "line_total")
    readonly_fields = ("unit_price", "line_total")
    autocomplete_fields = ("variant",)

    @admin.display(description="Unit price")
    def unit_price(self, obj):
        return obj.unit_price if obj.pk else "-"

    @admin.display(description="Line total")
    def line_total(self, obj):
        return obj.line_total if obj.pk else "-"


@admin.register(Cart)
class CartAdmin(admin.ModelAdmin):
    list_display = ("__str__", "owner", "line_count", "unit_count", "coupon_code", "updated_at")
    list_filter = ("created_at", "updated_at")
    search_fields = ("user__email", "user__full_name", "session_key", "coupon_code")
    readonly_fields = ("session_key", "created_at", "updated_at")
    list_select_related = ("user",)
    date_hierarchy = "updated_at"
    inlines = [CartItemInline]

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .annotate(_lines=Count("items", distinct=True), _units=Sum("items__quantity"))
        )

    @admin.display(description="Customer")
    def owner(self, obj) -> str:
        return obj.user.email if obj.user_id else "Guest"

    @admin.display(description="Lines", ordering="_lines")
    def line_count(self, obj) -> int:
        return obj._lines or 0

    @admin.display(description="Units", ordering="_units")
    def unit_count(self, obj) -> int:
        return obj._units or 0

    def has_add_permission(self, request) -> bool:
        return False


class WishlistItemInline(admin.TabularInline):
    model = WishlistItem
    extra = 0
    autocomplete_fields = ("product",)


@admin.register(Wishlist)
class WishlistAdmin(admin.ModelAdmin):
    list_display = ("user", "item_count", "updated_at")
    search_fields = ("user__email", "user__full_name")
    list_select_related = ("user",)
    inlines = [WishlistItemInline]

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_items=Count("items"))

    @admin.display(description="Saved items", ordering="_items")
    def item_count(self, obj) -> int:
        return obj._items or 0

    def has_add_permission(self, request) -> bool:
        return False
