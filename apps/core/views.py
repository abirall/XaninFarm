"""Homepage, static pages, error handlers and robots.txt."""

from __future__ import annotations

from django.db import DatabaseError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.cache import cache_control
from django.views.decorators.http import require_GET

from apps.core.models import HeroSlide, Page, PromoBanner, SiteSetting, Testimonial, ValueProposition


@require_GET
def home(request: HttpRequest) -> HttpResponse:
    """The storefront homepage: hero, categories, featured produce, story, proof."""
    from apps.inventory.services import annotate_product_stock
    from apps.products.models import Category, Product

    # Annotated once here so every card can show availability without the
    # per-variant stock query that Product.is_in_stock would trigger.
    products = annotate_product_stock(Product.objects.storefront())

    context = {
        "hero_slides": HeroSlide.objects.active(),
        "value_props": ValueProposition.objects.active(),
        "testimonials": Testimonial.objects.active()[:6],
        "categories": Category.objects.top_level_active(),
        "featured_products": products.filter(is_featured=True)[:8],
        "new_arrivals": products.order_by("-published_at", "-id")[:8],
        "banners": [
            banner
            for banner in PromoBanner.objects.active().filter(
                placement__in=[PromoBanner.Placement.HOME_MID, PromoBanner.Placement.HOME_BOTTOM]
            )
            if banner.is_live()
        ],
    }
    return render(request, "core/home.html", context)


@require_GET
def page_detail(request: HttpRequest, slug: str) -> HttpResponse:
    """Editor-managed static page (About, FAQ, Terms, Privacy...)."""
    page = get_object_or_404(Page, slug=slug, is_published=True)
    return render(request, "core/page.html", {"page": page})


@require_GET
@cache_control(max_age=60 * 60 * 24)
def robots_txt(request: HttpRequest) -> HttpResponse:
    """Allow crawling of the catalogue, keep private and transactional URLs out."""
    lines = [
        "User-agent: *",
        "Disallow: /account/",
        "Disallow: /cart/",
        "Disallow: /checkout/",
        "Disallow: /orders/",
        "Disallow: /payments/",
        "Disallow: /dashboard/",
        "Disallow: /django-admin/",
        "Disallow: /api/",
        "Allow: /",
        "",
        f"Sitemap: {request.build_absolute_uri('/sitemap.xml')}",
    ]
    return HttpResponse("\n".join(lines), content_type="text/plain; charset=utf-8")


@require_GET
def health(request: HttpRequest) -> HttpResponse:
    """Liveness/readiness probe for Docker, Nginx and CI smoke tests."""
    from django.db import connection

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except DatabaseError:
        return HttpResponse("database unavailable", status=503, content_type="text/plain")
    return HttpResponse("ok", content_type="text/plain")


# ---------------------------------------------------------------------------
# Error handlers
#
# Registered in config/urls.py. Each renders a branded page and must never
# raise - hence the defensive settings lookup.
# ---------------------------------------------------------------------------
def _error(request: HttpRequest, template: str, status: int) -> HttpResponse:
    try:
        site = SiteSetting.get_solo()
    except DatabaseError:
        site = None
    return render(request, template, {"site": site}, status=status)


def bad_request(request, exception=None):
    return _error(request, "errors/400.html", 400)


def permission_denied(request, exception=None):
    return _error(request, "errors/403.html", 403)


def page_not_found(request, exception=None):
    return _error(request, "errors/404.html", 404)


def server_error(request):
    return _error(request, "errors/500.html", 500)
