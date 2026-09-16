"""Read-only catalogue endpoints.

The filtering here deliberately mirrors ``ProductListView`` in
``apps.products.views``: the same query parameters, the same sort keys, the same
``SORT_OPTIONS`` table. A shopper and an API client asking the same question
must get the same answer, so there is one definition of "sorted by price".
"""

from __future__ import annotations

from django.db.models import F
from rest_framework import generics
from rest_framework.permissions import AllowAny

from apps.inventory.services import annotate_product_stock, available_quantity_map
from apps.products.models import Category, Product
from apps.products.serializers import (
    CategoryTreeSerializer,
    ProductDetailSerializer,
    ProductListSerializer,
)
from apps.products.views import SORT_OPTIONS, DEFAULT_SORT, _decimal_or_none


class CategoryListAPIView(generics.ListAPIView):
    """Active top-level categories with their children."""

    serializer_class = CategoryTreeSerializer
    permission_classes = [AllowAny]
    pagination_class = None  # A short, stable list; paging it helps nobody.

    def get_queryset(self):
        return Category.objects.nav_menu()


class ProductListAPIView(generics.ListAPIView):
    serializer_class = ProductListSerializer
    permission_classes = [AllowAny]
    # Filtering is done by hand below rather than through DjangoFilterBackend,
    # so that it stays identical to the HTML shop.
    filter_backends = []

    def get_queryset(self):
        params = self.request.query_params
        queryset = annotate_product_stock(Product.objects.storefront().with_price_range())

        slug = params.get("category")
        if slug:
            category = Category.objects.filter(slug=slug, is_active=True).first()
            # An unknown category returns nothing rather than everything: a
            # typo must not silently widen the result set.
            queryset = queryset.in_category(category) if category else queryset.none()

        search = (params.get("q") or "").strip()[:120]
        if search:
            queryset = queryset.search(search)

        min_price = _decimal_or_none(params.get("min_price"))
        if min_price is not None:
            queryset = queryset.filter(min_price__gte=min_price)

        max_price = _decimal_or_none(params.get("max_price"))
        if max_price is not None:
            queryset = queryset.filter(min_price__lte=max_price)

        if params.get("in_stock") == "1":
            queryset = queryset.filter(available_stock__gt=0)

        if params.get("on_offer") == "1":
            queryset = queryset.filter(
                variants__is_active=True,
                variants__compare_at_price__gt=F("variants__price"),
            ).distinct()

        rating = params.get("rating")
        if rating in {"3", "4", "5"}:
            queryset = queryset.filter(rating_average__gte=int(rating))

        sort = params.get("sort") if params.get("sort") in SORT_OPTIONS else DEFAULT_SORT
        return queryset.order_by(*SORT_OPTIONS[sort][1])


class ProductDetailAPIView(generics.RetrieveAPIView):
    serializer_class = ProductDetailSerializer
    permission_classes = [AllowAny]
    lookup_field = "slug"

    def get_queryset(self):
        # storefront() is what keeps an unpublished or deactivated product a
        # 404 here, exactly as it is on the website.
        return (
            Product.objects.storefront()
            .select_related("category")
            .prefetch_related(
                "images",
                "variants",
            )
        )

    def get_object(self):
        # Cached because get_serializer_context() also needs the instance, and
        # DRF would otherwise re-run the lookup and its permission checks.
        if not hasattr(self, "_object"):
            self._object = super().get_object()
        return self._object

    def get_serializer_context(self) -> dict:
        context = super().get_serializer_context()
        # One bulk stock query for the whole variant list, rather than one per
        # variant from ProductVariant.available_stock.
        context["stock_map"] = available_quantity_map(self.get_object().active_variants)
        return context
