"""Root URL configuration for XaninFarm."""

from __future__ import annotations

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

admin.site.site_header = "XaninFarm Administration"
admin.site.site_title = "XaninFarm"
admin.site.index_title = "Farm operations"

urlpatterns = [
    # --- Storefront ---
    path("", include("apps.core.urls", namespace="core")),
    path("shop/", include("apps.products.urls", namespace="products")),
    path("cart/", include("apps.cart.urls", namespace="cart")),
    path("account/", include("apps.accounts.urls", namespace="accounts")),
    path("checkout/", include("apps.orders.urls_checkout", namespace="checkout")),
    path("orders/", include("apps.orders.urls", namespace="orders")),
    path("payments/", include("apps.payments.urls", namespace="payments")),
    path("reviews/", include("apps.reviews.urls", namespace="reviews")),
    # --- Staff dashboard (login + staff required) ---
    path("dashboard/", include("apps.core.urls_dashboard", namespace="dashboard")),
    # --- JSON API ---
    path("api/v1/", include("config.api_urls")),
    # --- Django admin ---
    path("django-admin/", admin.site.urls),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

    try:
        import debug_toolbar  # noqa: F401
    except ImportError:
        pass
    else:
        urlpatterns += [path("__debug__/", include("debug_toolbar.urls"))]

# Custom error handlers (templates live in templates/errors/).
handler400 = "apps.core.views.bad_request"
handler403 = "apps.core.views.permission_denied"
handler404 = "apps.core.views.page_not_found"
handler500 = "apps.core.views.server_error"
