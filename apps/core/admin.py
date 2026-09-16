"""Django admin registrations for site settings and homepage content."""

from __future__ import annotations

from django.contrib import admin
from django.utils.html import format_html

from apps.core.models import (
    HeroSlide,
    Page,
    PromoBanner,
    SiteSetting,
    Testimonial,
    ValueProposition,
)


class NoAddIfExistsMixin:
    """Prevent creating a second row for singleton models."""

    def has_add_permission(self, request) -> bool:
        return not self.model.objects.exists()

    def has_delete_permission(self, request, obj=None) -> bool:
        return False


@admin.register(SiteSetting)
class SiteSettingAdmin(NoAddIfExistsMixin, admin.ModelAdmin):
    fieldsets = (
        ("Brand", {"fields": ("brand_name", "tagline", "about_short")}),
        (
            "Announcement bar",
            {"fields": ("announcement_active", "announcement_text", "announcement_url")},
        ),
        (
            "Contact",
            {
                "fields": (
                    "support_phone",
                    "whatsapp_number",
                    "support_email",
                    "farm_address",
                    "opening_hours",
                )
            },
        ),
        ("Social", {"fields": ("facebook_url", "instagram_url", "youtube_url")}),
        (
            "Commerce rules",
            {
                "fields": (
                    "default_delivery_fee",
                    "free_delivery_threshold",
                    "min_order_amount",
                    "cod_enabled",
                )
            },
        ),
    )


class ImagePreviewMixin:
    """Render a small thumbnail in list views."""

    image_field = "image"

    @admin.display(description="Preview")
    def image_preview(self, obj):
        image = getattr(obj, self.image_field, None)
        if not image:
            return "-"
        return format_html(
            '<img src="{}" style="height:40px;width:64px;object-fit:cover;border-radius:4px" />',
            image.url,
        )


@admin.register(HeroSlide)
class HeroSlideAdmin(ImagePreviewMixin, admin.ModelAdmin):
    list_display = ("image_preview", "title", "eyebrow", "is_active", "sort_order")
    list_editable = ("is_active", "sort_order")
    list_filter = ("is_active",)
    search_fields = ("title", "subtitle")


@admin.register(ValueProposition)
class ValuePropositionAdmin(admin.ModelAdmin):
    list_display = ("title", "icon", "is_active", "sort_order")
    list_editable = ("is_active", "sort_order")
    list_filter = ("is_active", "icon")


@admin.register(Testimonial)
class TestimonialAdmin(admin.ModelAdmin):
    list_display = ("author_name", "author_location", "rating", "is_active", "sort_order")
    list_editable = ("is_active", "sort_order")
    list_filter = ("is_active", "rating")
    search_fields = ("author_name", "quote")


@admin.register(PromoBanner)
class PromoBannerAdmin(ImagePreviewMixin, admin.ModelAdmin):
    list_display = ("image_preview", "title", "placement", "is_active", "starts_at", "ends_at")
    list_filter = ("placement", "is_active")
    search_fields = ("title", "subtitle")


@admin.register(Page)
class PageAdmin(admin.ModelAdmin):
    list_display = ("title", "slug", "is_published", "show_in_footer", "sort_order", "updated_at")
    list_editable = ("is_published", "show_in_footer", "sort_order")
    list_filter = ("is_published", "show_in_footer")
    prepopulated_fields = {"slug": ("title",)}
    search_fields = ("title", "body")
