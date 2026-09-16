"""Storefront catalogue views: shop listing, category pages, product detail."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.core.paginator import Paginator
from django.db.models import F, Prefetch
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET
from django.views.generic import ListView

from apps.core.mixins import HtmxResponseMixin
from apps.inventory.services import annotate_product_stock, available_quantity_map
from apps.products.models import Category, Product, ProductImage, ProductVariant

#: Keep in step with apps.reviews.views.PAGE_SIZE - the product page renders
#: the first page, and the "load more" fragment continues from it.
REVIEWS_PER_PAGE = 10

# value -> (label, orm ordering)
SORT_OPTIONS: dict[str, tuple[str, tuple[str, ...]]] = {
    "featured": ("Featured", ("-is_featured", "-published_at")),
    "newest": ("Newest first", ("-published_at", "-id")),
    "price_asc": ("Price: low to high", ("min_price", "name")),
    "price_desc": ("Price: high to low", ("-min_price", "name")),
    "rating": ("Top rated", ("-rating_average", "-rating_count")),
    "name": ("Name A-Z", ("name",)),
}
DEFAULT_SORT = "featured"


def _decimal_or_none(raw: str | None) -> Decimal | None:
    if not raw:
        return None
    try:
        value = Decimal(raw)
    except (InvalidOperation, TypeError, ValueError):
        return None
    return value if value >= 0 else None


class ProductListView(HtmxResponseMixin, ListView):
    """The shop: search, filter, sort and paginate the catalogue.

    HTMX requests receive just the results fragment so filtering feels instant
    without a full page reload.
    """

    template_name = "products/product_list.html"
    htmx_template_name = "products/partials/_product_results.html"
    context_object_name = "products"
    paginate_by = 12

    def get_queryset(self):
        params = self.request.GET
        queryset = Product.objects.storefront().with_price_range()
        queryset = annotate_product_stock(queryset)

        # --- Category ---
        self.active_category = None
        slug = self.kwargs.get("slug") or params.get("category")
        if slug:
            self.active_category = get_object_or_404(Category, slug=slug, is_active=True)
            queryset = queryset.in_category(self.active_category)

        # --- Search ---
        self.search_term = (params.get("q") or "").strip()[:120]
        if self.search_term:
            queryset = queryset.search(self.search_term)

        # --- Price range ---
        self.min_price = _decimal_or_none(params.get("min_price"))
        self.max_price = _decimal_or_none(params.get("max_price"))
        if self.min_price is not None:
            queryset = queryset.filter(min_price__gte=self.min_price)
        if self.max_price is not None:
            queryset = queryset.filter(min_price__lte=self.max_price)

        # --- Availability and offers ---
        self.in_stock_only = params.get("in_stock") == "1"
        if self.in_stock_only:
            queryset = queryset.filter(available_stock__gt=0)

        self.on_offer_only = params.get("on_offer") == "1"
        if self.on_offer_only:
            # "On offer" means a strike-through price above the selling price.
            queryset = queryset.filter(
                variants__is_active=True,
                variants__compare_at_price__gt=F("variants__price"),
            ).distinct()

        # --- Minimum rating ---
        self.min_rating = params.get("rating")
        if self.min_rating in {"3", "4", "5"}:
            queryset = queryset.filter(rating_average__gte=Decimal(self.min_rating))

        # --- Sorting ---
        self.sort = params.get("sort") if params.get("sort") in SORT_OPTIONS else DEFAULT_SORT
        return queryset.order_by(*SORT_OPTIONS[self.sort][1])

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # One bulk stock query for every variant on the page, instead of one
        # query per variant from the ProductVariant.available_stock property.
        products = list(context["products"])
        variant_ids = [
            variant.pk for product in products for variant in product.active_variants
        ]
        context["stock_map"] = available_quantity_map(variant_ids)

        context.update(
            {
                "active_category": self.active_category,
                "categories": Category.objects.nav_menu(),
                "search_term": self.search_term,
                "sort": self.sort,
                "sort_options": SORT_OPTIONS,
                "min_price": self.min_price,
                "max_price": self.max_price,
                "in_stock_only": self.in_stock_only,
                "on_offer_only": self.on_offer_only,
                "min_rating": self.min_rating,
                "result_count": context["paginator"].count if context.get("paginator") else 0,
                "has_active_filters": any(
                    [
                        self.search_term,
                        self.active_category,
                        self.min_price is not None,
                        self.max_price is not None,
                        self.in_stock_only,
                        self.on_offer_only,
                        self.min_rating in {"3", "4", "5"},
                    ]
                ),
            }
        )
        return context


@require_GET
def product_detail(request: HttpRequest, slug: str) -> HttpResponse:
    """Product page: gallery, variant picker, stock, reviews and related items."""
    from apps.reviews.forms import ReviewForm
    from apps.reviews.models import Review

    queryset = (
        Product.objects.storefront()
        .select_related("category", "category__parent")
        .prefetch_related(
            Prefetch("images", queryset=ProductImage.objects.order_by("-is_primary", "sort_order")),
            Prefetch(
                "variants",
                queryset=ProductVariant.objects.filter(is_active=True).order_by(
                    "sort_order", "price"
                ),
            ),
        )
    )
    product = get_object_or_404(queryset, slug=slug)

    variants = product.active_variants
    stock_map = available_quantity_map(variants)

    reviews = (
        Review.objects.approved()
        .filter(product=product)
        .select_related("user")
        .order_by("-created_at")
    )
    # Paginated rather than sliced so the reviews block on this page and the
    # HTMX fragment in apps.reviews render from the same shape of context.
    # `review_page` (not `page`) keeps the no-JS pager from colliding with any
    # other paginated block on the page. get_page() absorbs junk input.
    review_page = Paginator(reviews, REVIEWS_PER_PAGE).get_page(
        request.GET.get("review_page", 1)
    )

    related = annotate_product_stock(
        Product.objects.storefront()
        .with_price_range()
        .filter(category=product.category)
        .exclude(pk=product.pk)
    )[:4]

    # Whether the signed-in customer is allowed to leave a review.
    can_review = False
    has_reviewed = False
    is_wishlisted = False
    if request.user.is_authenticated:
        from apps.cart.services import wishlisted_product_ids
        from apps.reviews.services import can_user_review, has_reviewed as user_has_reviewed

        can_review = can_user_review(request.user, product)
        has_reviewed = user_has_reviewed(request.user, product)
        is_wishlisted = product.pk in wishlisted_product_ids(request.user)

    context = {
        "product": product,
        "variants": variants,
        "stock_map": stock_map,
        "selected_variant": product.default_variant,
        "reviews": review_page.object_list,
        "page_obj": review_page,
        "review_count": review_page.paginator.count,
        "rating_breakdown": Review.objects.rating_breakdown(product),
        "form": ReviewForm(),
        "can_review": can_review,
        "has_reviewed": has_reviewed,
        "is_wishlisted": is_wishlisted,
        "related_products": related,
        "breadcrumbs": _breadcrumbs(product),
    }
    return render(request, "products/product_detail.html", context)


def _breadcrumbs(product: Product) -> list[dict[str, str]]:
    trail = [{"label": "Shop", "url": "/shop/"}]
    category = product.category
    if category.parent_id:
        trail.append({"label": category.parent.name, "url": category.parent.get_absolute_url()})
    trail.append({"label": category.name, "url": category.get_absolute_url()})
    trail.append({"label": product.name, "url": ""})
    return trail


@require_GET
def variant_panel(request: HttpRequest, pk: int) -> HttpResponse:
    """HTMX fragment: price, stock and add-to-cart state for one variant.

    Swapped in when the shopper picks a different pack size, so price and
    availability always come from the server rather than the page's markup.
    """
    variant = get_object_or_404(
        ProductVariant.objects.select_related("product").sellable(), pk=pk
    )
    available = available_quantity_map([variant]).get(variant.pk)
    if available is None:  # pragma: no cover - defensive
        raise Http404

    return render(
        request,
        "products/partials/_variant_panel.html",
        {"product": variant.product, "variant": variant, "available": available},
    )
