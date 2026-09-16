"""Checkout, order history and order tracking."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Prefetch
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.views.generic import ListView

from apps.cart.services import CartError, add_item, get_cart, quote_cart
from apps.core.utils import client_ip
from apps.delivery.services import available_slots, resolve_zone_for_address
from apps.orders.forms import CheckoutForm, OrderCancelForm
from apps.orders.models import Order, OrderItem
from apps.orders.services import CheckoutError, cancel_order_by_customer, place_order
from apps.payments.constants import PaymentMethod


# ---------------------------------------------------------------------------
# Checkout
# ---------------------------------------------------------------------------
def _zone_for_address(address):
    if address is None:
        return None
    return address.delivery_zone or resolve_zone_for_address(address)


def _selected_address(request, form: CheckoutForm):
    """The address the customer has picked, or their default."""
    raw = request.POST.get("address") or request.GET.get("address")
    queryset = request.user.addresses.select_related("delivery_zone")
    if raw:
        address = queryset.filter(pk=raw).first()
        if address is not None:
            return address
    return queryset.filter(is_default=True).first() or queryset.first()


@login_required
def checkout(request: HttpRequest) -> HttpResponse:
    cart = get_cart(request, create=False)
    if cart is None or cart.is_empty:
        messages.info(request, "Your cart is empty - add something fresh first.")
        return redirect("products:list")

    if not request.user.addresses.exists():
        messages.info(request, "Add a delivery address to continue.")
        return redirect(f"{reverse('accounts:address_create')}?next={reverse('checkout:start')}")

    form = CheckoutForm(request.POST or None, user=request.user)

    if request.method == "POST" and form.is_valid():
        try:
            order = place_order(
                user=request.user,
                cart=cart,
                address=form.cleaned_data["address"],
                payment_method=form.cleaned_data["payment_method"],
                delivery_slot=form.cleaned_data.get("delivery_slot"),
                requested_delivery_date=form.cleaned_data.get("requested_delivery_date"),
                customer_note=form.cleaned_data.get("customer_note", ""),
                ip_address=client_ip(request),
            )
        except CheckoutError as exc:
            messages.error(request, exc.message)
        else:
            if order.payment_method == PaymentMethod.COD:
                return redirect("orders:confirmation", number=order.number)
            return redirect("payments:start", number=order.number)

    address = _selected_address(request, form)
    zone = _zone_for_address(address)
    quote = quote_cart(cart, zone=zone, user=request.user, check_stock=True)

    return render(
        request,
        "orders/checkout.html",
        {
            "form": form,
            "cart": cart,
            "quote": quote,
            "zone": zone,
            "selected_address": address,
            "slots": available_slots(zone),
        },
    )


@login_required
def checkout_summary(request: HttpRequest) -> HttpResponse:
    """HTMX fragment: re-price the order when the address changes.

    The delivery fee is recomputed here from the chosen zone, never sent by
    the browser.
    """
    cart = get_cart(request, create=False)
    address = _selected_address(request, CheckoutForm(user=request.user))
    zone = _zone_for_address(address)
    quote = quote_cart(cart, zone=zone, user=request.user, check_stock=True)
    return render(
        request,
        "orders/partials/_checkout_summary.html",
        {"quote": quote, "zone": zone, "slots": available_slots(zone)},
    )


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------
class OrderListView(LoginRequiredMixin, ListView):
    template_name = "orders/order_list.html"
    context_object_name = "orders"
    paginate_by = 10

    def get_queryset(self):
        return (
            Order.objects.for_user(self.request.user)
            .prefetch_related(
                Prefetch("items", queryset=OrderItem.objects.select_related("variant"))
            )
            .select_related("delivery_zone")
        )


def _owned_order(request, number: str) -> Order:
    """Fetch an order that belongs to the caller, or 404.

    A 404 rather than a 403 so order numbers cannot be probed.
    """
    return get_object_or_404(
        Order.objects.placed()
        .select_related("delivery_zone", "delivery_slot", "coupon")
        .prefetch_related("items", "items__variant", "items__variant__product"),
        number=number,
        user=request.user,
    )


@login_required
def order_detail(request: HttpRequest, number: str) -> HttpResponse:
    order = _owned_order(request, number)
    return render(
        request,
        "orders/order_detail.html",
        {
            "order": order,
            "events": order.events.filter(is_customer_visible=True).order_by("created_at"),
            "cancel_form": OrderCancelForm(),
            "payments": order.payments.all(),
        },
    )


@login_required
def order_confirmation(request: HttpRequest, number: str) -> HttpResponse:
    order = _owned_order(request, number)
    return render(request, "orders/order_confirmation.html", {"order": order})


def order_track(request: HttpRequest, number: str, token: str) -> HttpResponse:
    """Public tracking page reached from the confirmation email.

    Authorised by an unguessable per-order token rather than a login, so the
    link works for whoever the customer forwards it to.
    """
    order = (
        Order.objects.placed()
        .select_related("delivery_zone")
        .prefetch_related("items")
        .filter(number=number, access_token=token)
        .first()
    )
    if order is None:
        raise Http404
    return render(
        request,
        "orders/order_track.html",
        {
            "order": order,
            "events": order.events.filter(is_customer_visible=True).order_by("created_at"),
            "public": True,
        },
    )


@require_POST
@login_required
def order_cancel(request: HttpRequest, number: str) -> HttpResponse:
    order = _owned_order(request, number)
    form = OrderCancelForm(request.POST)
    reason = form.cleaned_data["reason"] if form.is_valid() else ""
    try:
        cancel_order_by_customer(order, user=request.user, reason=reason)
    except CheckoutError as exc:
        messages.error(request, exc.message)
    else:
        messages.success(request, f"Order {order.number} has been cancelled.")
    return redirect("orders:detail", number=order.number)


@require_POST
@login_required
def order_reorder(request: HttpRequest, number: str) -> HttpResponse:
    """Put a past order's still-available items back in the cart."""
    order = _owned_order(request, number)
    cart = get_cart(request)

    added, skipped = 0, []
    for item in order.items.select_related("variant", "variant__product"):
        try:
            add_item(cart, item.variant, item.quantity)
        except CartError:
            skipped.append(item.display_name)
        else:
            added += 1

    if added:
        messages.success(request, f"{added} item(s) added back to your cart.")
    if skipped:
        messages.warning(request, "Not available right now: " + ", ".join(skipped))
    return redirect("cart:detail")
