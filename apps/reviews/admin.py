"""Django admin for reviews - a moderation queue more than a form."""

from __future__ import annotations

from django.contrib import admin, messages
from django.utils import timezone
from django.utils.html import format_html

from apps.reviews.models import Review, ReviewStatus
from apps.reviews.services import moderate_review, refresh_product_rating


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ("stars", "product", "author", "status", "is_verified_purchase", "created_at")
    list_filter = ("status", "rating", "is_verified_purchase", "created_at")
    search_fields = ("product__name", "user__email", "title", "body")
    date_hierarchy = "created_at"
    actions = ("approve", "reject")
    autocomplete_fields = ("product",)
    readonly_fields = (
        "product",
        "user",
        "order_item",
        "rating",
        "title",
        "body",
        "is_verified_purchase",
        "created_at",
        "updated_at",
    )
    fieldsets = (
        (None, {"fields": ("product", "user", "order_item", "is_verified_purchase")}),
        ("Review", {"fields": ("rating", "title", "body", "created_at")}),
        ("Moderation", {"fields": ("status", "staff_reply", "replied_at", "replied_by")}),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("product", "user")

    def has_add_permission(self, request) -> bool:
        # Reviews come from customers who bought the product.
        return False

    @admin.display(description="Rating", ordering="rating")
    def stars(self, obj: Review) -> str:
        return format_html("<span title='{} of 5'>{}</span>", obj.rating, "★" * obj.rating)

    @admin.display(description="Customer", ordering="user__email")
    def author(self, obj: Review) -> str:
        return obj.user.email

    def save_model(self, request, obj, form, change):
        if obj.staff_reply and not obj.replied_at:
            obj.replied_at = timezone.now()
            obj.replied_by = request.user
        super().save_model(request, obj, form, change)
        # Publishing or hiding a review changes the product's average.
        refresh_product_rating(obj.product)

    @admin.action(description="Publish selected reviews")
    def approve(self, request, queryset):
        count = 0
        for review in queryset.exclude(status=ReviewStatus.APPROVED):
            moderate_review(review, ReviewStatus.APPROVED, by=request.user)
            count += 1
        self.message_user(request, f"Published {count} review(s).", messages.SUCCESS)

    @admin.action(description="Reject selected reviews")
    def reject(self, request, queryset):
        count = 0
        for review in queryset.exclude(status=ReviewStatus.REJECTED):
            moderate_review(review, ReviewStatus.REJECTED, by=request.user)
            count += 1
        self.message_user(request, f"Rejected {count} review(s).", messages.WARNING)
