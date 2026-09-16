"""Payment URLs.

``webhook`` is deliberately the only unauthenticated write endpoint, and it
proves its own origin before it does anything.
"""

from __future__ import annotations

from django.urls import path

from apps.payments import views

app_name = "payments"

urlpatterns = [
    path("start/<str:number>/", views.payment_start, name="start"),
    path("return/<str:number>/", views.payment_return, name="return"),
    path("cancel/<str:number>/", views.payment_cancel, name="cancel"),
    path("webhook/<str:provider>/", views.webhook, name="webhook"),
    # Development gateway - 404s unless PAYMENT_SANDBOX is on.
    path("sandbox/<str:reference>/", views.mock_gateway, name="mock_gateway"),
]
