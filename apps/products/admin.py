"""Django admin for the catalogue."""

from __future__ import annotations

from django.contrib import admin
from django.db.models import Count
from django.utils.html import format_html

from apps.inventory.models import InventoryBatch
from apps.products.models import Category, Product, ProductImage, ProductVariant


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "parent", "product_count", "is_active", "show_in_nav", "sort_order")
    list_editable = ("is_active", "show_in_nav", "sort_order")
    list_filter = ("is_active", "show_in_nav", "is_featured", "parent")
    search_fields = ("name", "description")
    prepopulated_fields = {"slug": ("name",)}
    autocomplete_fields = ("parent",)

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_product_count=Count("products"))

    @admin.display(description="Products", ordering="_product_count")
    def product_count(self, obj) -> int:
        return obj._product_count


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 1
    fields = ("preview", "image", "alt_text", "is_primary", "sort_order")
    readonly_fields = ("preview",)

    @admin.display(description="Preview")
    def preview(self, obj):
        if not obj.pk or not obj.image:
            return "-"
        return format_html(
            '<img src="{}" style="height:56px;width:56px;object-fit:cover;border-radius:6px" />',
            obj.image.url,
        )


class ProductVariantInline(admin.TabularInline):
    model = ProductVariant
    extra = 1
    fields = (
        "name",
        "sku",
        "pack_size",
        "unit",
        "price",
        "compare_at_price",
        "cost_price",
        "weight_grams",
        "is_active",
        "sort_order",
    )
    show_change_link = True


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "category",
        "variant_count",
        "stock_summary",
        "is_perishable",
        "is_active",
        "is_featured",
        "rating_average",
        "published_at",
    )
    list_editable = ("is_active", "is_featured")
    list_filter = ("is_active", "is_featured", "is_perishable", "category")
    search_fields = ("name", "short_description", "description", "variants__sku")
    prepopulated_fields = {"slug": ("name",)}
    autocomplete_fields = ("category",)
    readonly_fields = ("rating_average", "rating_count", "created_at", "updated_at")
    inlines = [ProductVariantInline, ProductImageInline]
    date_hierarchy = "published_at"

    fieldsets = (
        ("Basics", {"fields": ("category", "name", "slug", "short_description", "description")}),
        (
            "Farm details",
            {"fields": ("highlights", "farm_note", "storage_instructions")},
        ),
        (
            "Perishability",
            {
                "fields": ("is_perishable", "default_shelf_life_days"),
                "description": "Perishable products require an expiry date on every stock batch.",
            },
        ),
        ("Visibility", {"fields": ("is_active", "is_featured", "published_at")}),
        ("SEO", {"fields": ("seo_title", "seo_description"), "classes": ("collapse",)}),
        (
            "Reviews",
            {"fields": ("rating_average", "rating_count"), "classes": ("collapse",)},
        ),
        ("Audit", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("category")
            .annotate(_variant_count=Count("variants", distinct=True))
        )

    @admin.display(description="Variants", ordering="_variant_count")
    def variant_count(self, obj) -> int:
        return obj._variant_count

    @admin.display(description="Sellable stock")
    def stock_summary(self, obj):
        total = (
            InventoryBatch.objects.filter(variant__product=obj)
            .sellable()
            .on_hand_total()
        )
        colour = "#B91C1C" if total <= 0 else ("#A16207" if total <= 10 else "#15803D")
        return format_html('<span style="color:{};font-weight:600">{}</span>', colour, total)


@admin.register(ProductVariant)
class ProductVariantAdmin(admin.ModelAdmin):
    list_display = (
        "sku",
        "product",
        "name",
        "price",
        "compare_at_price",
        "available_stock_display",
        "is_active",
    )
    list_filter = ("is_active", "unit", "product__category")
    search_fields = ("sku", "name", "product__name")
    autocomplete_fields = ("product",)
    list_select_related = ("product",)

    @admin.display(description="Available")
    def available_stock_display(self, obj):
        total = obj.available_stock
        colour = "#B91C1C" if total <= 0 else ("#A16207" if total <= 10 else "#15803D")
        return format_html('<span style="color:{};font-weight:600">{}</span>', colour, total)
