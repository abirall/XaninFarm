"""Review URLs."""

from __future__ import annotations

from django.urls import path

from apps.reviews import views

app_name = "reviews"

urlpatterns = [
    path("product/<slug:slug>/", views.review_list, name="list"),
    path("product/<slug:slug>/write/", views.review_create, name="create"),
    path("<int:pk>/delete/", views.review_delete, name="delete"),
]
