"""Staff operations dashboard.

This is the day-to-day working surface for the farm team: what needs packing,
what is running out, what expires tomorrow, who has not paid. It deliberately
does *not* duplicate Django admin, which remains the place to edit catalogue
content, pages, banners and coupons.

Every view here is staff-only. Nothing in this module mutates stock, money or
order status directly - each action delegates to the service layer, which owns
the transactions and row locking.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from apps.analytics.services import (
    customer_summary,
    order_status_counts,
    outstanding_cash,
    payment_method_split,
    revenue_summary,
    sales_series,
    top_categories,
    top_products,
)
from apps.core.decorators import staff_required
from apps.inventory.forms import AdjustBatchForm, ReceiveStockForm
from apps.inventory.models import BatchStatus, InventoryBatch, StockMovement
from apps.inventory.services import (
    adjust_batch,
    expiring_batches,
    low_stock_variants,
    receive_stock,
)
from apps.orders.forms import OrderStatusForm
from apps.orders.models import Order, OrderStatus
from apps.orders.services import CheckoutError, transition_order
from apps.payments.constants import PaymentMethod, PaymentStatus
from apps.payments.models import Payment
from apps.reviews.models import Review, ReviewStatus
from apps.reviews.services import ReviewError, moderate_review, reply_to_review

PAGE_SIZE = 25


def _paginate(request: HttpRequest, queryset, per_page: int = PAGE_SIZE):
    """Paginate, absorbing junk page numbers rather than 404ing a staff tool."""
    return Paginator(queryset, per_page).get_page(request.GET.get("page"))


# ---------------------------------------------------------------------------
# Home
# ---------------------------------------------------------------------------
@staff_required
def home(request: HttpRequest) -> HttpResponse:
    """Today at a glance, plus the queues that need someone to act."""
    status_counts = order_status_counts()

    # The work queue: orders that are waiting on the farm, oldest first, because
    # the oldest unpacked order is always the most urgent one.
    action_needed = (
        Order.objects.placed()
        .filter(
            status__in=[
                OrderStatus.PENDING,
                OrderStatus.CONFIRMED,
                OrderStatus.PREPARING,
                OrderStatus.PACKED,
            ]
        )
        .select_related("delivery_zone")
        .order_by("placed_at")[:10]
    )

    series = sales_series(days=14)

    context = {
        "summary": revenue_summary(days=30),
        "series": series,
        # The chart scales every bar against the busiest day. Computed here
        # rather than in the template so the bar heights need no custom filter,
        # and defaulted to 1 so a week with no trade divides safely.
        "peak_revenue": max((day["revenue"] for day in series), default=0) or 1,
        "status_counts": status_counts,
        "open_orders": sum(
            status_counts.get(status, 0)
            for status in (
                OrderStatus.PENDING,
                OrderStatus.CONFIRMED,
                OrderStatus.PREPARING,
                OrderStatus.PACKED,
                OrderStatus.OUT_FOR_DELIVERY,
            )
        ),
        "action_needed": action_needed,
        "outstanding_cash": outstanding_cash(),
        "customers": customer_summary(days=30),
        "low_stock": low_stock_variants()[:8],
        "expiring": expiring_batches()[:8],
        "pending_reviews": Review.objects.pending().count(),
    }
    return render(request, "dashboard/home.html", context)


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------
@staff_required
def orders(request: HttpRequest) -> HttpResponse:
    """Order queue, filterable by fulfilment status and payment status."""
    queryset = Order.objects.placed().select_related("user", "delivery_zone")

    status = request.GET.get("status")
    if status in OrderStatus.values:
        queryset = queryset.filter(status=status)

    payment_status = request.GET.get("payment_status")
    if payment_status in PaymentStatus.values:
        queryset = queryset.filter(payment_status=payment_status)

    search = (request.GET.get("q") or "").strip()[:80]
    if search:
        queryset = queryset.filter(
            Q(number__icontains=search)
            | Q(ship_recipient__icontains=search)
            | Q(contact_phone__icontains=search)
            | Q(contact_email__icontains=search)
        )

    return render(
        request,
        "dashboard/orders.html",
        {
            "page_obj": _paginate(request, queryset),
            "status_counts": order_status_counts(),
            "status_choices": OrderStatus.choices,
            "payment_status_choices": PaymentStatus.choices,
            "active_status": status,
            "active_payment_status": payment_status,
            "search": search,
        },
    )


@staff_required
def order_detail(request: HttpRequest, number: str) -> HttpResponse:
    order = get_object_or_404(
        Order.objects.select_related(
            "user", "delivery_zone", "delivery_slot", "delivery_partner", "coupon"
        ).prefetch_related("items", "payments", "events__created_by"),
        number=number,
    )
    return render(
        request,
        "dashboard/order_detail.html",
        {
            "order": order,
            "events": order.events.order_by("created_at"),
            "payments": order.payments.all(),
            "status_form": OrderStatusForm(order=order),
        },
    )


@require_POST
@staff_required
def order_status(request: HttpRequest, number: str) -> HttpResponse:
    """Advance an order. The service layer enforces the allowed transitions."""
    order = get_object_or_404(Order.objects.placed(), number=number)
    form = OrderStatusForm(request.POST, order=order)

    if not form.is_valid():
        messages.error(request, "That is not a step this order can take next.")
    else:
        try:
            transition_order(
                order,
                form.cleaned_data["status"],
                by=request.user,
                note=form.cleaned_data.get("note", ""),
            )
        except CheckoutError as exc:
            messages.error(request, exc.message)
        else:
            messages.success(request, f"{order.number} moved to {order.get_status_display()}.")

    return redirect("dashboard:order_detail", number=order.number)


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------
@staff_required
def inventory(request: HttpRequest) -> HttpResponse:
    """What is running out and what is about to expire."""
    return render(
        request,
        "dashboard/inventory.html",
        {
            "low_stock": low_stock_variants(),
            "expiring": expiring_batches(),
            "expired_count": InventoryBatch.objects.expired()
            .filter(quantity_available__gt=0)
            .count(),
        },
    )


@staff_required
def batches(request: HttpRequest) -> HttpResponse:
    """The batch register, filterable by state."""
    queryset = InventoryBatch.objects.select_related(
        "variant", "variant__product"
    ).order_by("expiry_date", "received_date")

    state = request.GET.get("state")
    if state == "expiring":
        queryset = queryset.filter(status=BatchStatus.ACTIVE, quantity_available__gt=0)
        queryset = queryset.filter(pk__in=expiring_batches().values("pk"))
    elif state == "expired":
        queryset = queryset.expired()
    elif state == "depleted":
        queryset = queryset.filter(quantity_available__lte=0)
    elif state == "active":
        queryset = queryset.filter(status=BatchStatus.ACTIVE, quantity_available__gt=0)

    search = (request.GET.get("q") or "").strip()[:80]
    if search:
        queryset = queryset.filter(
            Q(batch_number__icontains=search)
            | Q(variant__sku__icontains=search)
            | Q(variant__product__name__icontains=search)
        )

    return render(
        request,
        "dashboard/batches.html",
        {
            "page_obj": _paginate(request, queryset),
            "state": state,
            "search": search,
            # ("", label) is the unfiltered tab; query_replace drops empty values.
            "states": [
                ("", "All"),
                ("active", "Active"),
                ("expiring", "Expiring"),
                ("expired", "Expired"),
                ("depleted", "Depleted"),
            ],
        },
    )


@staff_required
def batch_receive(request: HttpRequest) -> HttpResponse:
    """Goods in. Creates a batch or tops up an existing batch number."""
    form = ReceiveStockForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        batch = receive_stock(
            variant=data["variant"],
            quantity=data["quantity"],
            batch_number=data["batch_number"],
            expiry_date=data.get("expiry_date"),
            production_date=data.get("production_date"),
            received_date=data.get("received_date"),
            unit_cost=data.get("unit_cost"),
            supplier=data.get("supplier", ""),
            storage_location=data.get("storage_location", ""),
            notes=data.get("notes", ""),
            performed_by=request.user,
        )
        messages.success(request, f"Booked in {data['quantity']} to batch {batch.batch_number}.")
        return redirect("dashboard:batches")

    return render(request, "dashboard/batch_receive.html", {"form": form})


@staff_required
def batch_detail(request: HttpRequest, pk: int) -> HttpResponse:
    """One batch, its movement history, and the stock-count correction form."""
    batch = get_object_or_404(
        InventoryBatch.objects.select_related("variant", "variant__product"), pk=pk
    )
    form = AdjustBatchForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        adjust_batch(
            batch=batch,
            new_available=form.cleaned_data["new_available"],
            reason=form.cleaned_data["reason"],
            performed_by=request.user,
        )
        messages.success(request, f"Batch {batch.batch_number} corrected.")
        return redirect("dashboard:batch_detail", pk=batch.pk)

    return render(
        request,
        "dashboard/batch_detail.html",
        {
            "batch": batch,
            "form": form,
            "movements": StockMovement.objects.filter(batch=batch)
            .select_related("performed_by")
            .order_by("-created_at")[:50],
        },
    )


# ---------------------------------------------------------------------------
# Customers, payments, reviews
# ---------------------------------------------------------------------------
@staff_required
def customers(request: HttpRequest) -> HttpResponse:
    queryset = (
        get_user_model()
        .objects.filter(is_staff=False)
        .annotate(
            order_count=Count("orders", filter=~Q(orders__status=OrderStatus.DRAFT)),
            spent=Sum("orders__total", filter=Q(orders__status=OrderStatus.DELIVERED)),
        )
        .order_by("-date_joined")
    )

    search = (request.GET.get("q") or "").strip()[:80]
    if search:
        queryset = queryset.filter(
            Q(full_name__icontains=search)
            | Q(email__icontains=search)
            | Q(phone__icontains=search)
        )

    return render(
        request,
        "dashboard/customers.html",
        {
            "page_obj": _paginate(request, queryset),
            "search": search,
            "summary": customer_summary(days=30),
        },
    )


@staff_required
def payments(request: HttpRequest) -> HttpResponse:
    queryset = Payment.objects.select_related("order", "recorded_by").order_by("-created_at")

    status = request.GET.get("status")
    if status in PaymentStatus.values:
        queryset = queryset.filter(status=status)

    provider = request.GET.get("provider")
    if provider:
        queryset = queryset.filter(provider=provider)

    return render(
        request,
        "dashboard/payments.html",
        {
            "page_obj": _paginate(request, queryset),
            "status_choices": PaymentStatus.choices,
            "active_status": status,
            "active_provider": provider,
            "outstanding_cash": outstanding_cash(),
        },
    )


@staff_required
def reviews(request: HttpRequest) -> HttpResponse:
    """Moderation queue. Defaults to what is waiting, not to everything."""
    queryset = Review.objects.select_related("product", "user").order_by("-created_at")

    status = request.GET.get("status", ReviewStatus.PENDING)
    if status in ReviewStatus.values:
        queryset = queryset.filter(status=status)

    return render(
        request,
        "dashboard/reviews.html",
        {
            "page_obj": _paginate(request, queryset),
            "status_choices": ReviewStatus.choices,
            "active_status": status,
            "pending_count": Review.objects.pending().count(),
        },
    )


@require_POST
@staff_required
def review_moderate(request: HttpRequest, pk: int) -> HttpResponse:
    """Approve or reject a review, and optionally reply to it."""
    review = get_object_or_404(Review, pk=pk)
    reply = (request.POST.get("staff_reply") or "").strip()
    status = request.POST.get("status")

    try:
        if reply:
            reply_to_review(review, reply=reply, by=request.user)
        if status:
            moderate_review(review, status, by=request.user)
    except ReviewError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Review updated.")

    # `next` comes from the form, so it is validated against this host before
    # being followed - an unchecked redirect target is an open redirect.
    next_url = request.POST.get("next", "")
    if url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return redirect(next_url)
    return redirect("dashboard:reviews")


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------
@staff_required
def analytics(request: HttpRequest) -> HttpResponse:
    """Sales reporting over a selectable window."""
    try:
        days = int(request.GET.get("days", 30))
    except (TypeError, ValueError):
        days = 30
    # Clamp rather than trust: an unbounded window is an easy accidental
    # table scan on a shop with years of trade.
    days = max(7, min(days, 365))

    # The aggregate returns raw column values ("cod"); swap in the human label
    # here so the template does not have to know the payment vocabulary.
    method_labels = dict(PaymentMethod.choices)
    payment_split = [
        {**row, "payment_method": method_labels.get(row["payment_method"], row["payment_method"])}
        for row in payment_method_split(days=days)
    ]

    return render(
        request,
        "dashboard/analytics.html",
        {
            "days": days,
            "summary": revenue_summary(days=days),
            "series": sales_series(days=days),
            "top_products": top_products(days=days),
            "top_categories": top_categories(days=days),
            "payment_split": payment_split,
            "customers": customer_summary(days=days),
            "window_options": [7, 30, 90, 365],
        },
    )
