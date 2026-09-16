"""Cart and wishlist storage.

Carts hold quantities and nothing else about money. Prices are read live
from the variant every time a total is computed, so a price change is
reflected immediately and a stale client cannot pin an old price.
"""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel
from apps.core.utils import ZERO, money

MAX_QUANTITY = getattr(settings, "CART_MAX_QUANTITY_PER_ITEM", 50)


class Cart(TimeStampedModel):
    """One open basket per signed-in customer, or per anonymous session."""

    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="carts",
        verbose_name=_("customer"),
    )
    session_key = models.CharField(_("session key"), max_length=40, blank=True, db_index=True)
    coupon_code = models.CharField(
        _("coupon code"),
        max_length=32,
        blank=True,
        help_text=_("Re-validated on every page load; never trusted as a stored discount."),
    )

    class Meta:
        ordering = ("-updated_at",)
        verbose_name = _("cart")
        verbose_name_plural = _("carts")
        constraints = [
            models.UniqueConstraint(
                fields=["user"],
                condition=models.Q(user__isnull=False),
                name="unique_open_cart_per_user",
            ),
            models.UniqueConstraint(
                fields=["session_key"],
                condition=models.Q(user__isnull=True) & ~models.Q(session_key=""),
                name="unique_open_cart_per_session",
            ),
        ]

    def __str__(self) -> str:
        owner = self.user.email if self.user_id else f"guest:{self.session_key[:8]}"
        return f"Cart #{self.pk} ({owner})"

    # --- Totals ---------------------------------------------------------------
    @property
    def item_count(self) -> int:
        """Number of distinct lines."""
        return len(self.items.all()) if self._items_prefetched else self.items.count()

    @property
    def total_quantity(self) -> int:
        if self._items_prefetched:
            return sum(item.quantity for item in self.items.all())
        return self.items.aggregate(total=models.Sum("quantity"))["total"] or 0

    @property
    def subtotal(self) -> Decimal:
        """Live goods total. Requires variants to be selected/prefetched."""
        return money(sum((item.line_total for item in self.items.all()), ZERO))

    @property
    def is_empty(self) -> bool:
        return not self.items.exists()

    @property
    def _items_prefetched(self) -> bool:
        return "items" in getattr(self, "_prefetched_objects_cache", {})

    def line_items(self):
        """Items with everything the templates and pricing need, in one query."""
        return self.items.select_related(
            "variant", "variant__product", "variant__product__category"
        ).order_by("created_at", "id")


class CartItem(TimeStampedModel):
    cart = models.ForeignKey(
        Cart, on_delete=models.CASCADE, related_name="items", verbose_name=_("cart")
    )
    variant = models.ForeignKey(
        "products.ProductVariant",
        on_delete=models.PROTECT,
        related_name="cart_items",
        verbose_name=_("variant"),
    )
    quantity = models.PositiveIntegerField(
        _("quantity"),
        default=1,
        validators=[MinValueValidator(1), MaxValueValidator(MAX_QUANTITY)],
    )

    class Meta:
        ordering = ("created_at", "id")
        verbose_name = _("cart item")
        verbose_name_plural = _("cart items")
        constraints = [
            models.UniqueConstraint(fields=["cart", "variant"], name="unique_variant_per_cart"),
            models.CheckConstraint(
                condition=models.Q(quantity__gte=1), name="cart_item_quantity_positive"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.quantity} x {self.variant}"

    @property
    def unit_price(self) -> Decimal:
        """Always the live variant price - carts never store money."""
        return money(self.variant.price)

    @property
    def compare_at_price(self) -> Decimal | None:
        return money(self.variant.compare_at_price) if self.variant.compare_at_price else None

    @property
    def line_total(self) -> Decimal:
        return money(self.unit_price * self.quantity)

    @property
    def line_savings(self) -> Decimal:
        if not self.variant.is_on_offer:
            return ZERO
        return money((money(self.variant.compare_at_price) - self.unit_price) * self.quantity)


class Wishlist(TimeStampedModel):
    user = models.OneToOneField(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="wishlist",
        verbose_name=_("customer"),
    )

    class Meta:
        verbose_name = _("wishlist")
        verbose_name_plural = _("wishlists")

    def __str__(self) -> str:
        return f"Wishlist for {self.user.email}"


class WishlistItem(TimeStampedModel):
    wishlist = models.ForeignKey(
        Wishlist, on_delete=models.CASCADE, related_name="items", verbose_name=_("wishlist")
    )
    product = models.ForeignKey(
        "products.Product",
        on_delete=models.CASCADE,
        related_name="wishlisted_by",
        verbose_name=_("product"),
    )

    class Meta:
        ordering = ("-created_at",)
        verbose_name = _("wishlist item")
        verbose_name_plural = _("wishlist items")
        constraints = [
            models.UniqueConstraint(
                fields=["wishlist", "product"], name="unique_product_per_wishlist"
            )
        ]

    def __str__(self) -> str:
        return f"{self.product} in wishlist {self.wishlist_id}"
