"""Staff dashboard routes (`/dashboard/`).

Every view in ``apps.core.dashboard_views`` is wrapped in ``staff_required``,
so there is no unguarded entry point in this namespace.
"""

from __future__ import annotations

from django.urls import path

from apps.core import dashboard_views as views

app_name = "dashboard"

urlpatterns = [
    path("", views.home, name="home"),
    # --- Orders ---
    path("orders/", views.orders, name="orders"),
    path("orders/<str:number>/", views.order_detail, name="order_detail"),
    path("orders/<str:number>/status/", views.order_status, name="order_status"),
    # --- Inventory ---
    path("inventory/", views.inventory, name="inventory"),
    path("inventory/batches/", views.batches, name="batches"),
    path("inventory/batches/receive/", views.batch_receive, name="batch_receive"),
    path("inventory/batches/<int:pk>/", views.batch_detail, name="batch_detail"),
    # --- People and money ---
    path("customers/", views.customers, name="customers"),
    path("payments/", views.payments, name="payments"),
    # --- Reviews ---
    path("reviews/", views.reviews, name="reviews"),
    path("reviews/<int:pk>/moderate/", views.review_moderate, name="review_moderate"),
    # --- Reporting ---
    path("analytics/", views.analytics, name="analytics"),
]
