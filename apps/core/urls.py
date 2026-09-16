"""Public core routes: homepage, static pages, robots, sitemap, health."""

from __future__ import annotations

from django.contrib.sitemaps.views import sitemap
from django.urls import path

from apps.core import views
from apps.core.sitemaps import SITEMAPS

app_name = "core"

urlpatterns = [
    path("", views.home, name="home"),
    path("healthz/", views.health, name="health"),
    path("robots.txt", views.robots_txt, name="robots"),
    path(
        "sitemap.xml",
        sitemap,
        {"sitemaps": SITEMAPS},
        name="django.contrib.sitemaps.views.sitemap",
    ),
    # Keep the catch-all slug last so it cannot shadow the routes above.
    path("p/<slug:slug>/", views.page_detail, name="page"),
]
