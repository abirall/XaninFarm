"""Cart endpoints.

Every response is a freshly computed quote, so a client never has to do
arithmetic and never has a stale total. All mutation goes through
``apps.cart.services``, which is the same code path the HTML views use.

The cart is resolved from the session or the signed-in user - never from a
cart id in the request - so one customer cannot address another's basket.
"""

from __future__ import annotations

from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.cart.models import CartItem
from apps.cart.serializers import (
    AddCartItemSerializer,
    CartQuoteSerializer,
    CouponSerializer,
    UpdateCartItemSerializer,
)
from apps.cart.services import (
    CartError,
    add_item,
    apply_coupon,
    clear_cart,
    get_cart,
    quote_cart,
    remove_coupon,
    remove_item,
    set_quantity,
)
from apps.coupons.services import CouponError
from apps.delivery.services import resolve_zone_for_address


def _zone_for(request):
    """The delivery zone used to price this quote.

    Anonymous callers get the default fee; a signed-in customer is priced
    against their default address, which is what the checkout page will use.
    """
    user = request.user
    if not user.is_authenticated:
        return None
    address = user.default_address
    if address is None:
        return None
    return address.delivery_zone or resolve_zone_for_address(address)


def _quote_response(request, cart, *, http_status: int = status.HTTP_200_OK) -> Response:
    quote = quote_cart(
        cart,
        zone=_zone_for(request),
        user=request.user if request.user.is_authenticated else None,
        check_stock=True,
    )
    return Response(CartQuoteSerializer(quote).data, status=http_status)


class CartAPIView(APIView):
    """GET the current basket; DELETE to empty it."""

    permission_classes = [AllowAny]

    def get(self, request):
        return _quote_response(request, get_cart(request, create=False))

    def delete(self, request):
        cart = get_cart(request, create=False)
        if cart is not None:
            clear_cart(cart)
        return _quote_response(request, cart)


class CartItemListAPIView(APIView):
    """POST a variant and quantity to add it to the basket."""

    permission_classes = [AllowAny]

    def post(self, request):
        serializer = AddCartItemSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        cart = get_cart(request)
        try:
            add_item(cart, serializer.validated_data["variant"], serializer.validated_data["quantity"])
        except CartError as exc:
            # Stock and sellability failures are the customer's to fix, so the
            # message is safe to show. Anything else propagates as a 500.
            return Response({"detail": exc.message}, status=status.HTTP_400_BAD_REQUEST)

        return _quote_response(request, cart, http_status=status.HTTP_201_CREATED)


class CartItemDetailAPIView(APIView):
    """PATCH a line's quantity, or DELETE the line."""

    permission_classes = [AllowAny]

    def _get_item(self, request, pk: int) -> CartItem | None:
        """The line, but only if it belongs to the caller's own cart."""
        cart = get_cart(request, create=False)
        if cart is None:
            return None
        return CartItem.objects.filter(cart=cart, pk=pk).select_related("variant").first()

    def patch(self, request, pk: int):
        item = self._get_item(request, pk)
        if item is None:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        serializer = UpdateCartItemSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            set_quantity(item.cart, item, serializer.validated_data["quantity"])
        except CartError as exc:
            return Response({"detail": exc.message}, status=status.HTTP_400_BAD_REQUEST)

        return _quote_response(request, item.cart)

    def delete(self, request, pk: int):
        item = self._get_item(request, pk)
        if item is None:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        cart = item.cart
        remove_item(cart, item)
        return _quote_response(request, cart)


class CartCouponAPIView(APIView):
    """POST a code to apply it, DELETE to remove it.

    The discount is never taken from the request - apply_coupon re-validates
    the code and recomputes the amount server-side on every call.
    """

    permission_classes = [AllowAny]

    def post(self, request):
        serializer = CouponSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        cart = get_cart(request)
        try:
            quote = apply_coupon(
                cart,
                serializer.validated_data["code"],
                user=request.user if request.user.is_authenticated else None,
                zone=_zone_for(request),
            )
        except CouponError as exc:
            # An invalid, expired or already-used code. The message is written
            # for the customer, so it is safe to return.
            return Response({"detail": exc.message}, status=status.HTTP_400_BAD_REQUEST)

        return Response(CartQuoteSerializer(quote).data)

    def delete(self, request):
        cart = get_cart(request, create=False)
        if cart is not None:
            remove_coupon(cart)
        return _quote_response(request, cart)
