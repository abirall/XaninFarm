"""Review API routes, mounted under /api/v1/ by config.api_urls.

Nested under the product because a review has no meaning without one. These
paths sit alongside apps.products.api_urls, which owns "products/<slug>/".
"""

from __future__ import annotations

from django.urls import path

from apps.reviews import api_views

urlpatterns = [
    path(
        "products/<slug:slug>/reviews/",
        api_views.ProductReviewListAPIView.as_view(),
        name="product-review-list",
    ),
    path(
        "products/<slug:slug>/reviews/mine/",
        api_views.ProductReviewCreateAPIView.as_view(),
        name="product-review-create",
    ),
]
