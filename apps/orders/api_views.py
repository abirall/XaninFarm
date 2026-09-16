"""Order endpoints.

Everything here is scoped to ``request.user``. A number belonging to someone
else is a 404 rather than a 403, so order numbers cannot be probed for
existence.

Placement goes through ``apps.orders.services.place_order``, which reserves
stock under a row lock and rolls the whole order back if any line cannot be
filled. There is no second implementation of that logic for the API.
"""

from __future__ import annotations

from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.cart.services import get_cart
from apps.core.utils import client_ip
from apps.orders.models import Order, OrderItem
from apps.orders.serializers import (
    CancelOrderSerializer,
    OrderDetailSerializer,
    OrderListSerializer,
    PlaceOrderSerializer,
)
from apps.orders.services import CheckoutError, cancel_order_by_customer, place_order


class OrderListAPIView(generics.ListAPIView):
    """The caller's own order history, newest first."""

    serializer_class = OrderListSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        from django.db.models import Prefetch

        return (
            Order.objects.for_user(self.request.user)
            .prefetch_related(Prefetch("items", queryset=OrderItem.objects.only("order", "quantity")))
            .order_by("-placed_at")
        )


class OrderDetailAPIView(generics.RetrieveAPIView):
    serializer_class = OrderDetailSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = "number"

    def get_queryset(self):
        # for_user() is the ownership check. Combined with lookup_field this
        # makes another customer's order a 404.
        return (
            Order.objects.for_user(self.request.user)
            .select_related("delivery_zone", "delivery_slot")
            .prefetch_related("items", "events")
        )


class PlaceOrderAPIView(APIView):
    """POST to turn the caller's cart into an order."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = PlaceOrderSerializer(data=request.data, user=request.user)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        cart = get_cart(request, create=False)
        if cart is None or cart.is_empty:
            return Response(
                {"detail": "Your cart is empty."}, status=status.HTTP_400_BAD_REQUEST
            )

        try:
            order = place_order(
                user=request.user,
                cart=cart,
                address=data["address"],
                payment_method=data["payment_method"],
                delivery_slot=data.get("delivery_slot"),
                requested_delivery_date=data.get("requested_delivery_date"),
                customer_note=data.get("customer_note", ""),
                ip_address=client_ip(request),
            )
        except CheckoutError as exc:
            # Out of stock, below the minimum, coupon no longer valid - all
            # things the customer can act on, so the message is returned.
            return Response({"detail": exc.message}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            OrderDetailSerializer(order, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


class CancelOrderAPIView(APIView):
    """POST to cancel an order that is still cancellable."""

    permission_classes = [IsAuthenticated]

    def post(self, request, number: str):
        order = Order.objects.for_user(request.user).filter(number=number).first()
        if order is None:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        serializer = CancelOrderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            # Re-checks ownership and the fulfilment stage, and returns the
            # stock and the coupon use.
            cancel_order_by_customer(
                order, user=request.user, reason=serializer.validated_data.get("reason", "")
            )
        except CheckoutError as exc:
            return Response({"detail": exc.message}, status=status.HTTP_400_BAD_REQUEST)

        order.refresh_from_db()
        return Response(OrderDetailSerializer(order, context={"request": request}).data)
