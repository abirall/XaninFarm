from django.urls import path

from apps.orders import views

app_name = "checkout"

urlpatterns = [
    path("", views.checkout, name="start"),
    path("summary/", views.checkout_summary, name="summary"),
]
