"""Cart serializers.

**Money is output-only here.** The writable surface is deliberately tiny:
a variant id, a quantity, a coupon code. Every price, discount, delivery fee
and total in the response is read from the server-computed ``CartQuote`` and
can never be supplied by the client.
"""

from __future__ import annotations

from rest_framework import serializers

from apps.cart.models import MAX_QUANTITY, CartItem
from apps.products.models import ProductVariant


class CartItemSerializer(serializers.ModelSerializer):
    """One basket line, priced live from the variant."""

    variant = serializers.IntegerField(source="variant_id", read_only=True)
    product = serializers.CharField(source="variant.product.name", read_only=True)
    product_slug = serializers.CharField(source="variant.product.slug", read_only=True)
    variant_name = serializers.CharField(source="variant.name", read_only=True)
    sku = serializers.CharField(source="variant.sku", read_only=True)
    display_unit = serializers.CharField(source="variant.display_unit", read_only=True)

    unit_price = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    line_total = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    line_savings = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = CartItem
        fields = [
            "id",
            "variant",
            "product",
            "product_slug",
            "variant_name",
            "sku",
            "display_unit",
            "quantity",
            "unit_price",
            "line_total",
            "line_savings",
        ]
        read_only_fields = fields


class StockIssueSerializer(serializers.Serializer):
    """A line the customer must fix before checkout. Read-only by nature."""

    item = serializers.IntegerField(source="item.pk", read_only=True)
    variant = serializers.IntegerField(source="item.variant_id", read_only=True)
    message = serializers.CharField(read_only=True)
    available = serializers.DecimalField(max_digits=12, decimal_places=3, read_only=True)


class CartQuoteSerializer(serializers.Serializer):
    """The whole basket as the server prices it.

    Mirrors ``apps.cart.services.CartQuote`` - the same object the checkout
    page and ``place_order`` read, so the API cannot disagree with either.
    """

    items = CartItemSerializer(many=True, read_only=True)

    subtotal = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    discount = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    delivery_fee = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    delivery_discount = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    total = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    savings = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    total_quantity = serializers.IntegerField(read_only=True)
    is_empty = serializers.BooleanField(read_only=True)
    meets_minimum = serializers.BooleanField(read_only=True)
    min_order_amount = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    amount_to_free_delivery = serializers.DecimalField(
        max_digits=12, decimal_places=2, read_only=True
    )
    has_free_delivery = serializers.BooleanField(read_only=True)
    is_checkoutable = serializers.BooleanField(read_only=True)
    cod_available = serializers.BooleanField(read_only=True)

    coupon_code = serializers.SerializerMethodField()
    coupon_error = serializers.CharField(read_only=True)
    stock_issues = StockIssueSerializer(many=True, read_only=True)

    def get_coupon_code(self, quote) -> str | None:
        return quote.coupon.code if quote.coupon else None


# ---------------------------------------------------------------------------
# Write serializers - the entire client-writable surface of the cart
# ---------------------------------------------------------------------------
class AddCartItemSerializer(serializers.Serializer):
    """Add to basket. Accepts a variant and a quantity, and nothing else."""

    variant = serializers.PrimaryKeyRelatedField(queryset=ProductVariant.objects.none())
    quantity = serializers.IntegerField(min_value=1, max_value=MAX_QUANTITY, default=1)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # sellable() is what stops an inactive or unpublished variant being
        # added by id - the same gate the HTML add-to-cart view uses.
        self.fields["variant"].queryset = ProductVariant.objects.sellable()


class UpdateCartItemSerializer(serializers.Serializer):
    """Set a line's quantity. Zero is accepted and removes the line."""

    quantity = serializers.IntegerField(min_value=0, max_value=MAX_QUANTITY)


class CouponSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=32, trim_whitespace=True)
