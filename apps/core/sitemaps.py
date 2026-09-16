"""Sitemaps for the public catalogue and static pages."""

from __future__ import annotations

from django.contrib.sitemaps import Sitemap
from django.urls import reverse


class StaticViewSitemap(Sitemap):
    changefreq = "weekly"
    priority = 0.6

    def items(self) -> list[str]:
        return ["core:home", "products:list"]

    def location(self, item: str) -> str:
        return reverse(item)


class PageSitemap(Sitemap):
    changefreq = "monthly"
    priority = 0.4

    def items(self):
        from apps.core.models import Page

        return Page.objects.filter(is_published=True)

    def lastmod(self, obj):
        return obj.updated_at


class CategorySitemap(Sitemap):
    changefreq = "weekly"
    priority = 0.7

    def items(self):
        from apps.products.models import Category

        return Category.objects.filter(is_active=True)

    def lastmod(self, obj):
        return obj.updated_at


class ProductSitemap(Sitemap):
    changefreq = "daily"
    priority = 0.9
    limit = 2000

    def items(self):
        from apps.products.models import Product

        return Product.objects.storefront()

    def lastmod(self, obj):
        return obj.updated_at


SITEMAPS = {
    "static": StaticViewSitemap,
    "pages": PageSitemap,
    "categories": CategorySitemap,
    "products": ProductSitemap,
}
