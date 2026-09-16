"""Payment endpoints.

Two of these views are reachable without a session, because a customer coming
back from a gateway may arrive on a cross-site POST that carries no cookie.
Neither of them trusts a single field it is handed: the gateway is always
re-asked server-side before anything is recorded.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST

from apps.core.utils import money
from apps.orders.models import Order
from apps.payments.constants import PaymentMethod, PaymentStatus
from apps.payments.models import Payment
from apps.payments.providers import PaymentError, SignatureError
from apps.payments.services import process_webhook, start_payment, verify_payment

logger = logging.getLogger(__name__)


def _owned_order(request, number: str) -> Order:
    """A 404 rather than a 403, so order numbers cannot be probed."""
    return get_object_or_404(Order.objects.select_related("user"), number=number, user=request.user)


def _open_order(number: str) -> Order:
    """Look up an order without a session - used only on gateway callbacks."""
    return get_object_or_404(Order, number=number)


@login_required
@require_http_methods(["GET", "POST"])
def payment_start(request, number: str):
    """Send the customer to their chosen gateway (or straight on, for COD)."""
    order = _owned_order(request, number)

    if order.payment_method == PaymentMethod.COD:
        return redirect("orders:confirmation", number=order.number)
    if order.is_paid:
        return redirect("orders:detail", number=order.number)

    try:
        intent = start_payment(order, request=request)
    except PaymentError as exc:
        messages.error(request, str(exc))
        return redirect("orders:detail", number=order.number)

    if intent.requires_redirect:
        return HttpResponseRedirect(intent.redirect_url)

    messages.info(request, intent.instructions or _("Your payment is being processed."))
    return redirect("orders:detail", number=order.number)


@csrf_exempt
@require_http_methods(["GET", "POST"])
def payment_return(request, number: str):
    """Where the gateway drops the customer once they are done.

    CSRF-exempt because gateways post here from their own domain - which is
    only safe because the request body is never believed. The payment is
    re-verified against the provider and the outcome comes from that call.
    """
    order = _open_order(number)
    payload = request.POST.dict() if request.method == "POST" else request.GET.dict()

    payment = (
        order.payments.exclude(provider=PaymentMethod.COD).order_by("-created_at").first()
    )
    if payment is None:
        raise Http404

    try:
        payment = verify_payment(payment, payload=payload)
    except PaymentError as exc:
        messages.error(request, str(exc))
    else:
        if payment.is_paid:
            messages.success(request, _("Payment received - thank you!"))
        elif payment.status == PaymentStatus.PENDING:
            messages.info(request, _("Your payment is still being confirmed."))
        else:
            messages.error(
                request, payment.failure_reason or _("That payment did not go through.")
            )

    if request.user.is_authenticated and order.user_id == request.user.pk:
        return redirect("orders:confirmation", number=order.number)
    # The session cookie did not survive a cross-site POST back from the
    # gateway. Send them to the tokenised tracking page instead of a login
    # wall - they have just come from paying for this exact order.
    return redirect(order.get_tracking_url())


@csrf_exempt
@require_http_methods(["GET", "POST"])
def payment_cancel(request, number: str):
    """The customer backed out on the gateway's own page."""
    order = _open_order(number)
    payment = (
        order.payments.filter(status=PaymentStatus.PENDING)
        .exclude(provider=PaymentMethod.COD)
        .order_by("-created_at")
        .first()
    )
    if payment is not None:
        payment.status = PaymentStatus.CANCELLED
        payment.failure_reason = "Cancelled by the customer."
        payment.save(update_fields=["status", "failure_reason", "updated_at"])

    messages.info(request, _("Payment cancelled. Your order is still waiting for you."))
    if request.user.is_authenticated and order.user_id == request.user.pk:
        return redirect("orders:detail", number=order.number)
    return redirect(order.get_tracking_url())


@csrf_exempt
@require_POST
def webhook(request, provider: str):
    """Provider callbacks. Verified, deduplicated, and deliberately terse."""
    try:
        event = process_webhook(provider, request)
    except SignatureError:
        # Nothing informative: an attacker learns only that it was refused.
        return HttpResponse(status=400)
    except PaymentError as exc:
        logger.warning("Webhook for %s could not be handled: %s", provider, exc)
        return HttpResponse(status=400)

    return HttpResponse(event.status, content_type="text/plain")


# ---------------------------------------------------------------------------
# Sandbox gateway
# ---------------------------------------------------------------------------
@require_http_methods(["GET", "POST"])
def mock_gateway(request, reference: str):
    """A stand-in for a hosted payment page, for development and tests.

    Refuses to exist outside sandbox mode, so there is no route to it in
    production even if something links here by mistake.
    """
    if not getattr(settings, "PAYMENT_SANDBOX", False):
        raise Http404

    payment = get_object_or_404(
        Payment.objects.select_related("order"),
        reference=reference,
        provider=PaymentMethod.MOCK,
    )

    if request.method == "POST":
        outcome = request.POST.get("outcome", "paid")
        if outcome not in {"paid", "failed", "cancelled"}:
            outcome = "failed"

        # Stand in for the gateway's own books: this is what ``verify()``
        # will read back, exactly as a real server-to-server call would.
        payment.raw_response = {
            **(payment.raw_response or {}),
            "sandbox": {
                "outcome": outcome,
                "transaction_id": f"SBX-{payment.reference[-8:]}",
                "amount": str(money(payment.amount)),
                "reason": "Declined by the sandbox gateway."
                if outcome == "failed"
                else "",
            },
        }
        payment.save(update_fields=["raw_response", "updated_at"])

        if outcome == "cancelled":
            return redirect("payments:cancel", number=payment.order.number)
        return redirect("payments:return", number=payment.order.number)

    return render(
        request,
        "payments/mock_gateway.html",
        {"payment": payment, "order": payment.order},
    )
