"""Public catalogue routes."""

from __future__ import annotations

from django.urls import path

from apps.products import views

app_name = "products"

urlpatterns = [
    path("", views.ProductListView.as_view(), name="list"),
    path("variant/<int:pk>/panel/", views.variant_panel, name="variant_panel"),
    path("category/<slug:slug>/", views.ProductListView.as_view(), name="category"),
    path("<slug:slug>/", views.product_detail, name="detail"),
]
