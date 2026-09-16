"""Versioned JSON API routes (`/api/v1/`)."""

from __future__ import annotations

from django.urls import include, path
from rest_framework.authtoken.views import obtain_auth_token

app_name = "api"

urlpatterns = [
    path("auth/token/", obtain_auth_token, name="auth-token"),
    path("", include("apps.products.api_urls")),
    path("", include("apps.cart.api_urls")),
    path("", include("apps.orders.api_urls")),
    path("", include("apps.reviews.api_urls")),
]
