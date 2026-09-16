"""Template context available on every page."""

from __future__ import annotations

from django.conf import settings
from django.db import DatabaseError

from apps.core.models import SiteSetting


def site_context(request) -> dict:
    """Expose brand settings and primary navigation to all templates.

    Deliberately defensive: this runs on every request including error pages,
    so a missing table (fresh database, mid-migration) must not raise.
    """
    try:
        site = SiteSetting.get_solo()
    except DatabaseError:
        site = None

    try:
        from apps.products.models import Category

        nav_categories = list(Category.objects.nav_menu())
    except DatabaseError:
        nav_categories = []

    footer_pages = []
    try:
        from apps.core.models import Page

        footer_pages = list(
            Page.objects.filter(is_published=True, show_in_footer=True).only("title", "slug")
        )
    except DatabaseError:
        pass

    return {
        "site": site,
        "site_name": getattr(site, "brand_name", settings.SITE_NAME),
        "site_tagline": getattr(site, "tagline", settings.SITE_TAGLINE),
        "nav_categories": nav_categories,
        "footer_pages": footer_pages,
        "currency_symbol": settings.CURRENCY_SYMBOL,
        "currency_code": settings.CURRENCY_CODE,
        "low_stock_threshold": settings.LOW_STOCK_THRESHOLD,
        "tailwind_cdn_fallback": getattr(settings, "TAILWIND_CDN_FALLBACK", False),
    }
