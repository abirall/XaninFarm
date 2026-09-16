"""Account routes: auth, profile, addresses, wishlist."""

from __future__ import annotations

from django.urls import path

from apps.accounts import views

app_name = "accounts"

urlpatterns = [
    # --- Authentication ---
    path("register/", views.RegisterView.as_view(), name="register"),
    path("login/", views.BrandedLoginView.as_view(), name="login"),
    path("logout/", views.BrandedLogoutView.as_view(), name="logout"),
    # --- Profile ---
    path("", views.profile, name="profile"),
    path("edit/", views.ProfileEditView.as_view(), name="profile_edit"),
    path("wishlist/", views.wishlist, name="wishlist"),
    # --- Addresses ---
    path("addresses/", views.AddressListView.as_view(), name="address_list"),
    path("addresses/new/", views.AddressCreateView.as_view(), name="address_create"),
    path("addresses/<int:pk>/edit/", views.AddressUpdateView.as_view(), name="address_edit"),
    path("addresses/<int:pk>/delete/", views.AddressDeleteView.as_view(), name="address_delete"),
    path("addresses/<int:pk>/default/", views.address_make_default, name="address_make_default"),
    # --- Password change ---
    path("password/change/", views.BrandedPasswordChangeView.as_view(), name="password_change"),
    path(
        "password/change/done/",
        views.BrandedPasswordChangeDoneView.as_view(),
        name="password_change_done",
    ),
    # --- Password reset ---
    path("password/reset/", views.BrandedPasswordResetView.as_view(), name="password_reset"),
    path(
        "password/reset/sent/",
        views.BrandedPasswordResetDoneView.as_view(),
        name="password_reset_done",
    ),
    path(
        "password/reset/<uidb64>/<token>/",
        views.BrandedPasswordResetConfirmView.as_view(),
        name="password_reset_confirm",
    ),
    path(
        "password/reset/complete/",
        views.BrandedPasswordResetCompleteView.as_view(),
        name="password_reset_complete",
    ),
]
