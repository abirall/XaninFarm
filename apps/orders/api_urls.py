"""Order API routes, mounted under /api/v1/ by config.api_urls."""

from __future__ import annotations

from django.urls import path

from apps.orders import api_views

urlpatterns = [
    path("orders/", api_views.OrderListAPIView.as_view(), name="order-list"),
    path("orders/place/", api_views.PlaceOrderAPIView.as_view(), name="order-place"),
    # Kept below "place/" so the literal path is matched first and an order can
    # never be numbered in a way that shadows it.
    path("orders/<str:number>/", api_views.OrderDetailAPIView.as_view(), name="order-detail"),
    path("orders/<str:number>/cancel/", api_views.CancelOrderAPIView.as_view(), name="order-cancel"),
]
