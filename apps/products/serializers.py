"""Catalogue serializers.

Read-only by design. The catalogue is edited in Django admin and the staff
dashboard, never over the API, so nothing here accepts input.

Stock is not a model field - it is computed from live inventory batches - so it
is supplied by the view through ``stock_map`` rather than read off the instance.
Doing it per-object would mean one query per variant.
"""

from __future__ import annotations

from decimal import Decimal

from rest_framework import serializers

from apps.core.utils import ZERO_QTY
from apps.products.models import Category, Product, ProductImage, ProductVariant


class CategorySerializer(serializers.ModelSerializer):
    url = serializers.CharField(source="get_absolute_url", read_only=True)

    class Meta:
        model = Category
        fields = ["id", "name", "slug", "full_name", "description", "url", "sort_order"]


class CategoryTreeSerializer(CategorySerializer):
    """Top-level categories with their children nested one level deep."""

    children = serializers.SerializerMethodField()

    class Meta(CategorySerializer.Meta):
        fields = [*CategorySerializer.Meta.fields, "children"]

    def get_children(self, category) -> list[dict]:
        # Categories nest exactly one level (enforced in Category.clean), so
        # this cannot recurse indefinitely.
        children = [child for child in category.children.all() if child.is_active]
        return CategorySerializer(children, many=True, context=self.context).data


class ProductImageSerializer(serializers.ModelSerializer):
    image = serializers.ImageField(read_only=True)

    class Meta:
        model = ProductImage
        fields = ["id", "image", "alt_text", "is_primary", "sort_order"]


class ProductVariantSerializer(serializers.ModelSerializer):
    display_unit = serializers.CharField(read_only=True)
    is_on_offer = serializers.BooleanField(read_only=True)
    discount_percent = serializers.IntegerField(read_only=True)
    available_stock = serializers.SerializerMethodField()
    in_stock = serializers.SerializerMethodField()

    class Meta:
        model = ProductVariant
        fields = [
            "id",
            "name",
            "sku",
            "pack_size",
            "unit",
            "display_unit",
            "price",
            "compare_at_price",
            "is_on_offer",
            "discount_percent",
            "available_stock",
            "in_stock",
        ]

    def _stock(self, variant) -> Decimal:
        """Availability from the view's bulk lookup.

        Fails closed: a variant missing from the map reports zero rather than
        falling back to a per-object query that might say it is sellable.
        """
        return self.context.get("stock_map", {}).get(variant.pk, ZERO_QTY)

    def get_available_stock(self, variant) -> str:
        return str(self._stock(variant))

    def get_in_stock(self, variant) -> bool:
        return self._stock(variant) > 0


class ProductListSerializer(serializers.ModelSerializer):
    """Grid card payload. Mirrors what templates/partials/_product_card.html shows."""

    category = serializers.CharField(source="category.name", read_only=True)
    category_slug = serializers.CharField(source="category.slug", read_only=True)
    url = serializers.CharField(source="get_absolute_url", read_only=True)
    price_from = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    price_to = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    has_price_range = serializers.BooleanField(read_only=True)
    compare_at_price = serializers.DecimalField(
        source="best_compare_at_price", max_digits=12, decimal_places=2, read_only=True
    )
    primary_image = serializers.SerializerMethodField()
    available_stock = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            "id",
            "name",
            "slug",
            "url",
            "short_description",
            "category",
            "category_slug",
            "price_from",
            "price_to",
            "has_price_range",
            "compare_at_price",
            "primary_image",
            "available_stock",
            "rating_average",
            "rating_count",
            "is_featured",
            "is_perishable",
        ]

    def get_primary_image(self, product) -> dict | None:
        image = product.primary_image
        if image is None:
            return None
        return ProductImageSerializer(image, context=self.context).data

    def get_available_stock(self, product) -> str:
        # Supplied by annotate_product_stock() in the list queryset.
        return str(getattr(product, "available_stock", ZERO_QTY))


class ProductDetailSerializer(ProductListSerializer):
    images = ProductImageSerializer(many=True, read_only=True)
    variants = serializers.SerializerMethodField()
    highlights = serializers.ListField(source="highlight_list", read_only=True)

    class Meta(ProductListSerializer.Meta):
        fields = [
            *ProductListSerializer.Meta.fields,
            "description",
            "highlights",
            "images",
            "variants",
            "default_shelf_life_days",
        ]

    def get_variants(self, product) -> list[dict]:
        return ProductVariantSerializer(
            product.active_variants, many=True, context=self.context
        ).data
