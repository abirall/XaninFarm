from django.urls import path

from apps.orders import views

app_name = "orders"

urlpatterns = [
    path("", views.OrderListView.as_view(), name="list"),
    path("<str:number>/", views.order_detail, name="detail"),
    path("<str:number>/confirmation/", views.order_confirmation, name="confirmation"),
    path("<str:number>/cancel/", views.order_cancel, name="cancel"),
    path("<str:number>/reorder/", views.order_reorder, name="reorder"),
    path("<str:number>/track/<str:token>/", views.order_track, name="track"),
]
