"""Cart and wishlist views. Every mutation is POST-only and CSRF protected."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.cart.models import CartItem
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
    toggle_wishlist,
)
from apps.core.mixins import htmx_trigger
from apps.coupons.services import CouponError
from apps.products.models import Product, ProductVariant


def _zone_for(request):
    """The delivery zone of the customer's default address, when we know it."""
    if not request.user.is_authenticated:
        return None
    address = request.user.addresses.select_related("delivery_zone").filter(is_default=True).first()
    return address.delivery_zone if address else None


def _render_cart(request, *, status: int = 200) -> HttpResponse:
    """Render the cart - the full page normally, the panel for HTMX."""
    cart = get_cart(request)
    quote = quote_cart(cart, zone=_zone_for(request), user=request.user, check_stock=True)
    template = (
        "cart/partials/_cart_panel.html" if request.htmx else "cart/cart_detail.html"
    )
    response = render(request, template, {"cart": cart, "quote": quote}, status=status)
    if request.htmx:
        htmx_trigger(response, "cart:changed")
    return response


def cart_detail(request: HttpRequest) -> HttpResponse:
    return _render_cart(request)


@require_POST
def cart_add(request: HttpRequest) -> HttpResponse:
    variant = get_object_or_404(
        ProductVariant.objects.select_related("product"),
        pk=request.POST.get("variant"),
    )
    cart = get_cart(request)
    try:
        add_item(cart, variant, request.POST.get("quantity", 1))
    except CartError as exc:
        messages.error(request, exc.message)
        if request.htmx:
            return _render_cart(request, status=422)
    else:
        messages.success(request, f"{variant.product.name} added to your cart.")

    if request.htmx:
        return _render_cart(request)
    return redirect(request.POST.get("next") or "cart:detail")


@require_POST
def cart_update(request: HttpRequest, pk: int) -> HttpResponse:
    cart = get_cart(request)
    item = get_object_or_404(
        CartItem.objects.select_related("variant", "variant__product"), pk=pk, cart=cart
    )
    try:
        set_quantity(cart, item, int(request.POST.get("quantity", 0) or 0))
    except (CartError, ValueError) as exc:
        message = getattr(exc, "message", "Enter a valid quantity.")
        messages.error(request, message)
        if request.htmx:
            return _render_cart(request, status=422)

    if request.htmx:
        return _render_cart(request)
    return redirect("cart:detail")


@require_POST
def cart_remove(request: HttpRequest, pk: int) -> HttpResponse:
    cart = get_cart(request)
    item = get_object_or_404(CartItem, pk=pk, cart=cart)
    remove_item(cart, item)
    messages.success(request, "Item removed.")
    if request.htmx:
        return _render_cart(request)
    return redirect("cart:detail")


@require_POST
def cart_clear(request: HttpRequest) -> HttpResponse:
    clear_cart(get_cart(request))
    messages.success(request, "Your cart is empty.")
    if request.htmx:
        return _render_cart(request)
    return redirect("cart:detail")


@require_POST
def coupon_apply(request: HttpRequest) -> HttpResponse:
    cart = get_cart(request)
    try:
        apply_coupon(
            cart,
            request.POST.get("code", ""),
            user=request.user,
            zone=_zone_for(request),
        )
    except CouponError as exc:
        messages.error(request, exc.message)
        if request.htmx:
            return _render_cart(request, status=422)
    else:
        messages.success(request, "Coupon applied.")

    if request.htmx:
        return _render_cart(request)
    return redirect(request.POST.get("next") or "cart:detail")


@require_POST
def coupon_remove(request: HttpRequest) -> HttpResponse:
    remove_coupon(get_cart(request))
    messages.success(request, "Coupon removed.")
    if request.htmx:
        return _render_cart(request)
    return redirect(request.POST.get("next") or "cart:detail")


@require_POST
@login_required
def wishlist_toggle(request: HttpRequest, pk: int) -> HttpResponse:
    product = get_object_or_404(Product.objects.filter(is_active=True), pk=pk)
    saved = toggle_wishlist(request.user, product)
    messages.success(request, "Saved to your wishlist." if saved else "Removed from your wishlist.")

    if request.htmx:
        response = render(
            request,
            "cart/partials/_wishlist_button.html",
            {"product": product, "is_wishlisted": saved},
        )
        htmx_trigger(response, "wishlist:changed")
        return response
    return redirect(request.POST.get("next") or product.get_absolute_url())
