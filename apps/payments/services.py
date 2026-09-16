"""Payment orchestration.

Everything money-related passes through here, and four rules hold throughout:

* **The provider decides, never the browser.** A status only ever comes from
  ``provider.verify()`` or a signature-checked webhook.
* **The amount is checked against the order.** A gateway that reports less
  than we asked for settles nothing - that is how hosted-page tampering is
  caught.
* **Webhooks are idempotent.** ``(provider, event_id)`` is unique, so a
  gateway may retry a notification as often as it likes.
* **Payment status and order status stay separate.** Money landing *may*
  advance fulfilment, but it is recorded either way.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.utils import ZERO, money, random_token
from apps.inventory.models import StockReservation
from apps.orders.models import Order, OrderStatus
from apps.payments.constants import PaymentMethod, PaymentStatus, TransactionKind
from apps.payments.models import Payment, Refund, WebhookEvent
from apps.payments.providers import (
    PaymentError,
    PaymentIntent,
    SignatureError,
    VerificationResult,
)
from apps.payments.providers.registry import build_provider, get_provider

logger = logging.getLogger(__name__)

#: Payment rows that represent money we actually received.
MONEY_RECEIVED = (
    PaymentStatus.PAID,
    PaymentStatus.PARTIALLY_REFUNDED,
    PaymentStatus.REFUNDED,
)


# ---------------------------------------------------------------------------
# Starting a payment
# ---------------------------------------------------------------------------
@transaction.atomic
def start_payment(order: Order, *, request=None) -> PaymentIntent:
    """Open (or resume) a payment attempt for an order."""
    order = Order.objects.select_for_update().get(pk=order.pk)

    if order.is_paid:
        raise PaymentError(_("This order has already been paid."))
    if order.is_closed:
        raise PaymentError(_("This order is closed."))
    if order.balance_due <= ZERO:
        raise PaymentError(_("There is nothing left to pay on this order."))

    provider = get_provider(order.payment_method)
    provider.ensure_configured()

    payment = (
        order.payments.select_for_update()
        .filter(provider=provider.code, status=PaymentStatus.PENDING, amount=order.balance_due)
        .order_by("-created_at")
        .first()
    )
    if payment is None:
        payment = Payment.objects.create(
            order=order, provider=provider.code, amount=order.balance_due
        )
    else:
        # Resuming an abandoned attempt: start the clock again.
        payment.initiated_at = timezone.now()
        payment.save(update_fields=["initiated_at", "updated_at"])

    return provider.initiate(payment, request=request)


# ---------------------------------------------------------------------------
# Recording an outcome
# ---------------------------------------------------------------------------
def verify_payment(payment: Payment, *, payload: dict | None = None, by=None) -> Payment:
    """Ask the provider what happened, then record it."""
    provider = build_provider(payment.provider)
    try:
        result = provider.verify(payment, payload=payload)
    except PaymentError:
        raise
    except Exception:  # pragma: no cover - network or provider faults
        logger.exception("Verification failed for payment %s", payment.reference)
        raise PaymentError(_("We could not confirm this payment. Please contact us."))
    return apply_verification(payment, result, by=by)


@transaction.atomic
def apply_verification(payment: Payment, result: VerificationResult, *, by=None) -> Payment:
    """Write a provider's verdict onto a payment and re-derive the order.

    Safe to call repeatedly with the same verdict, so a webhook and a browser
    return racing each other still move the money exactly once.
    """
    payment = Payment.objects.select_for_update().select_related("order").get(pk=payment.pk)

    if payment.status == result.status:
        return payment
    if payment.status in MONEY_RECEIVED:
        logger.info(
            "Ignoring %s verdict for already-settled payment %s",
            result.status,
            payment.reference,
        )
        return payment

    fields = ["status", "raw_response", "updated_at"]
    status = result.status

    if status == PaymentStatus.PAID and not _amount_is_sufficient(payment, result):
        status = PaymentStatus.FAILED
        payment.failure_reason = "Amount did not match the order."
        fields.append("failure_reason")
        logger.error(
            "Amount mismatch on %s: gateway said %s, order expects %s",
            payment.reference,
            result.amount,
            payment.amount,
        )
    elif result.failure_reason:
        payment.failure_reason = str(result.failure_reason)[:255]
        fields.append("failure_reason")

    payment.status = status
    payment.raw_response = {**(payment.raw_response or {}), "verification": result.raw}

    if result.provider_reference:
        payment.provider_reference = result.provider_reference
        fields.append("provider_reference")
    if result.instrument_hint:
        payment.instrument_hint = result.instrument_hint[:60]
        fields.append("instrument_hint")

    if status == PaymentStatus.PAID:
        payment.paid_at = timezone.now()
        fields.append("paid_at")
    elif status in {PaymentStatus.FAILED, PaymentStatus.CANCELLED}:
        payment.failed_at = timezone.now()
        fields.append("failed_at")

    payment.save(update_fields=list(dict.fromkeys(fields)))
    sync_order_payment_state(payment.order, by=by)
    return payment


def _amount_is_sufficient(payment: Payment, result: VerificationResult) -> bool:
    """A gateway may never settle an order for less than it costs.

    Paying *more* is allowed through - it happens with rounding on some
    wallets - and simply shows as an overpayment on the order.
    """
    if result.amount is None:
        return True
    return money(result.amount) >= money(payment.amount)


# ---------------------------------------------------------------------------
# Keeping the order in step
# ---------------------------------------------------------------------------
def _total(queryset, field: str = "amount") -> Decimal:
    return money(queryset.aggregate(total=Sum(field))["total"] or ZERO)


def _derive_payment_status(order: Order, *, paid: Decimal, refunded: Decimal) -> str:
    if refunded > ZERO:
        return PaymentStatus.REFUNDED if refunded >= paid else PaymentStatus.PARTIALLY_REFUNDED
    if paid > ZERO and paid >= order.total:
        return PaymentStatus.PAID
    if paid > ZERO:
        # Part-paid: still outstanding, and ``balance_due`` says by how much.
        return PaymentStatus.PENDING
    if order.payment_status == PaymentStatus.CANCELLED:
        return PaymentStatus.CANCELLED
    if order.payments.filter(status=PaymentStatus.FAILED).exists():
        return PaymentStatus.FAILED
    return PaymentStatus.PENDING


@transaction.atomic
def sync_order_payment_state(order: Order, *, by=None) -> Order:
    """Re-derive an order's payment columns from its payment rows.

    An order's money is always a *summary* of the payments beneath it, so a
    refund, a retried attempt and a manual entry all stay consistent without
    anyone adding to a running total by hand.
    """
    from apps.orders.services import CheckoutError, transition_order

    order = Order.objects.select_for_update().get(pk=order.pk)
    was_paid = order.payment_status == PaymentStatus.PAID

    paid = _total(order.payments.filter(status__in=MONEY_RECEIVED))
    refunded = _total(Refund.objects.filter(payment__order=order, status=PaymentStatus.REFUNDED))

    order.amount_paid = paid
    order.amount_refunded = refunded
    order.payment_status = _derive_payment_status(order, paid=paid, refunded=refunded)
    order.save(update_fields=["amount_paid", "amount_refunded", "payment_status", "updated_at"])

    if order.payment_status == PaymentStatus.PAID and not was_paid:
        # The money is in, so the stock hold must stop being able to time out.
        StockReservation.objects.filter(
            order=order, status=StockReservation.Status.HELD
        ).update(expires_at=None)

        if order.status == OrderStatus.PENDING:
            try:
                transition_order(order, OrderStatus.CONFIRMED, by=by, note="Payment received")
            except CheckoutError:  # pragma: no cover - staff moved it first
                logger.warning("Could not auto-confirm paid order %s", order.number)
    return order


# ---------------------------------------------------------------------------
# Cash on delivery
# ---------------------------------------------------------------------------
def record_cod_collection(order: Order, *, collected_by=None) -> Payment:
    """Log the cash a rider handed in.

    Only the ``Payment`` row is written here: the order's own totals are set
    by ``apps.orders.services`` as part of the delivery transition, so both
    are saved in one go.
    """
    existing = order.payments.filter(
        provider=PaymentMethod.COD, status=PaymentStatus.PAID
    ).first()
    if existing is not None:
        return existing

    return Payment.objects.create(
        order=order,
        provider=PaymentMethod.COD,
        amount=order.total,  # COD orders are unpaid until the rider collects.
        status=PaymentStatus.PAID,
        instrument_hint="Cash",
        paid_at=timezone.now(),
        recorded_by=collected_by,
    )


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------
def process_webhook(provider_code: str, request) -> WebhookEvent:
    """Validate, deduplicate and act on a gateway callback.

    Raises ``SignatureError`` for anything unproven; the view turns that into
    a bare 400 without explaining what was wrong.
    """
    provider = build_provider(provider_code)
    source_ip = request.META.get("REMOTE_ADDR")

    try:
        result = provider.parse_webhook(request)
    except SignatureError as exc:
        WebhookEvent.objects.create(
            provider=provider_code,
            event_id=f"rejected:{random_token(16)}",
            signature_valid=False,
            status=WebhookEvent.Status.REJECTED,
            error=str(exc)[:255],
            source_ip=source_ip,
        )
        logger.warning("Rejected %s webhook from %s: %s", provider_code, source_ip, exc)
        raise

    payment = Payment.objects.filter(
        provider=provider_code, reference=result.payment_reference
    ).first()

    try:
        with transaction.atomic():
            event = WebhookEvent.objects.create(
                provider=provider_code,
                event_id=result.event_id,
                kind=TransactionKind.PAYMENT,
                payment=payment,
                signature_valid=True,
                payload=result.raw,
                source_ip=source_ip,
            )
    except IntegrityError:
        # The unique (provider, event_id) constraint did its job.
        logger.info("Duplicate %s webhook %s ignored", provider_code, result.event_id)
        return WebhookEvent.objects.get(provider=provider_code, event_id=result.event_id)

    if payment is None:
        event.status = WebhookEvent.Status.REJECTED
        event.error = "No matching payment."
    else:
        try:
            if provider.webhook_is_authoritative:
                apply_verification(
                    payment,
                    VerificationResult(
                        status=result.status,
                        provider_reference=result.provider_reference,
                        amount=result.amount,
                        raw=result.raw,
                    ),
                )
            else:
                # A signature proves origin, not outcome: ask the gateway.
                verify_payment(payment, payload=result.raw)
        except PaymentError as exc:
            event.status = WebhookEvent.Status.FAILED
            event.error = str(exc)[:255]
        except Exception as exc:  # pragma: no cover - unexpected provider fault
            logger.exception("Webhook %s failed", result.event_id)
            event.status = WebhookEvent.Status.FAILED
            event.error = str(exc)[:255]
        else:
            event.status = WebhookEvent.Status.PROCESSED

    event.processed_at = timezone.now()
    event.save(update_fields=["status", "error", "processed_at", "updated_at"])
    return event


# ---------------------------------------------------------------------------
# Refunds
# ---------------------------------------------------------------------------
@transaction.atomic
def refund_payment(payment: Payment, *, amount: Decimal, reason: str = "", by=None) -> Refund:
    """Send money back through the gateway that took it."""
    payment = Payment.objects.select_for_update().select_related("order").get(pk=payment.pk)
    amount = money(amount)

    if amount <= ZERO:
        raise PaymentError(_("A refund must be for more than zero."))
    if amount > payment.refundable_amount:
        raise PaymentError(_("That is more than remains on this payment."))

    provider = build_provider(payment.provider)
    if not provider.supports_refund:
        raise PaymentError(
            _("%(provider)s refunds are handled outside the system.")
            % {"provider": provider.label}
        )

    result = provider.refund(payment, amount=amount, reason=reason)
    refund = Refund.objects.create(
        payment=payment,
        amount=amount,
        reason=reason[:255],
        provider_reference=result.provider_reference,
        status=result.status,
        raw_response=result.raw,
        created_by=by,
    )

    if result.status == PaymentStatus.REFUNDED:
        remaining = payment.refundable_amount
        payment.status = (
            PaymentStatus.REFUNDED if remaining <= ZERO else PaymentStatus.PARTIALLY_REFUNDED
        )
        payment.save(update_fields=["status", "updated_at"])
        sync_order_payment_state(payment.order, by=by)

    return refund
