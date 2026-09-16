"""Catalogue API routes, mounted under /api/v1/ by config.api_urls."""

from __future__ import annotations

from django.urls import path

from apps.products import api_views

urlpatterns = [
    path("categories/", api_views.CategoryListAPIView.as_view(), name="category-list"),
    path("products/", api_views.ProductListAPIView.as_view(), name="product-list"),
    path("products/<slug:slug>/", api_views.ProductDetailAPIView.as_view(), name="product-detail"),
]
