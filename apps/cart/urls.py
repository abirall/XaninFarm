from django.urls import path

from apps.cart import views

app_name = "cart"

urlpatterns = [
    path("", views.cart_detail, name="detail"),
    path("add/", views.cart_add, name="add"),
    path("items/<int:pk>/update/", views.cart_update, name="update"),
    path("items/<int:pk>/remove/", views.cart_remove, name="remove"),
    path("clear/", views.cart_clear, name="clear"),
    path("coupon/apply/", views.coupon_apply, name="coupon_apply"),
    path("coupon/remove/", views.coupon_remove, name="coupon_remove"),
    path("wishlist/<int:pk>/toggle/", views.wishlist_toggle, name="wishlist_toggle"),
]
