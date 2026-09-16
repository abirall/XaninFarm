"""Cart API routes, mounted under /api/v1/ by config.api_urls."""

from __future__ import annotations

from django.urls import path

from apps.cart import api_views

urlpatterns = [
    path("cart/", api_views.CartAPIView.as_view(), name="cart"),
    path("cart/items/", api_views.CartItemListAPIView.as_view(), name="cart-items"),
    path("cart/items/<int:pk>/", api_views.CartItemDetailAPIView.as_view(), name="cart-item"),
    path("cart/coupon/", api_views.CartCouponAPIView.as_view(), name="cart-coupon"),
]
