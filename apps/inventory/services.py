"""Stock allocation services.

Everything that changes stock goes through this module. Two rules are absolute:

1. Expired, quarantined or recalled stock is never allocated to an order.
2. Stock cannot be oversold, even under concurrent checkouts.

Rule 2 is enforced with pessimistic row locking. `reserve_stock` selects the
candidate batches ``FOR UPDATE`` in a deterministic FEFO order, so simultaneous
checkouts queue on the same rows rather than racing. Under PostgreSQL's default
READ COMMITTED isolation, a waiting transaction re-evaluates the
``quantity_available > 0`` predicate after it acquires the lock, so a batch
emptied by the winner is skipped by the loser - who then fails cleanly with
InsufficientStockError instead of driving the balance negative.

CHECK constraints on the table are the final backstop.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.utils import ZERO_QTY, to_quantity
from apps.inventory.models import BatchStatus, InventoryBatch, StockMovement, StockReservation

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class StockError(Exception):
    """Base class for stock problems that should be shown to the customer."""


class InsufficientStockError(StockError):
    """Raised when a variant cannot supply the requested quantity."""

    def __init__(self, variant, requested: Decimal, available: Decimal):
        self.variant = variant
        self.requested = to_quantity(requested)
        self.available = to_quantity(available)
        label = getattr(variant, "name", variant)
        product = getattr(getattr(variant, "product", None), "name", "")
        name = f"{product} - {label}".strip(" -")
        if self.available <= 0:
            message = _("%(name)s is out of stock.") % {"name": name}
        else:
            message = _("Only %(available)s of %(name)s left in stock.") % {
                "available": f"{self.available.normalize():f}",
                "name": name,
            }
        super().__init__(message)
        self.message = message


# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------
def _min_shelf_life_days() -> int:
    return getattr(settings, "MIN_SHELF_LIFE_ON_SALE_DAYS", 0)


def sellable_batches(variant, *, as_of: date | None = None):
    """FEFO-ordered batches that may be sold for this variant."""
    return (
        InventoryBatch.objects.filter(variant=variant)
        .sellable(as_of=as_of, min_shelf_life_days=_min_shelf_life_days())
        .fefo()
    )


def available_quantity(variant, *, as_of: date | None = None) -> Decimal:
    """Free-to-sell quantity for one variant, excluding expired batches."""
    total = sellable_batches(variant, as_of=as_of).aggregate(total=Sum("quantity_available"))[
        "total"
    ]
    return to_quantity(total or ZERO_QTY)


def available_quantity_map(variants: Iterable, *, as_of: date | None = None) -> dict[int, Decimal]:
    """Bulk availability lookup keyed by variant id.

    Product listings call this once per page instead of once per variant.
    """
    variant_ids = [v.pk if hasattr(v, "pk") else int(v) for v in variants]
    if not variant_ids:
        return {}

    as_of = as_of or timezone.localdate()
    cutoff = as_of + timedelta(days=_min_shelf_life_days())

    rows = (
        InventoryBatch.objects.filter(
            variant_id__in=variant_ids,
            status=BatchStatus.ACTIVE,
            quantity_available__gt=0,
        )
        .filter(Q(expiry_date__isnull=True) | Q(expiry_date__gte=cutoff))
        .values("variant_id")
        .annotate(
            total=Coalesce(
                Sum("quantity_available"),
                Value(ZERO_QTY),
                output_field=DecimalField(max_digits=12, decimal_places=3),
            )
        )
    )
    totals = {row["variant_id"]: to_quantity(row["total"]) for row in rows}
    return {variant_id: totals.get(variant_id, ZERO_QTY) for variant_id in variant_ids}


def has_stock(variant, quantity: Decimal | int) -> bool:
    return available_quantity(variant) >= to_quantity(quantity)


def annotate_product_stock(queryset, *, as_of: date | None = None):
    """Annotate a Product queryset with `available_stock` computed in SQL.

    Lets the shop filter and sort by availability without loading every batch.
    """
    from django.db.models import OuterRef, Subquery

    as_of = as_of or timezone.localdate()
    cutoff = as_of + timedelta(days=_min_shelf_life_days())

    per_product_stock = (
        InventoryBatch.objects.filter(
            variant__product=OuterRef("pk"),
            variant__is_active=True,
            status=BatchStatus.ACTIVE,
            quantity_available__gt=0,
        )
        .filter(Q(expiry_date__isnull=True) | Q(expiry_date__gte=cutoff))
        .values("variant__product")
        .annotate(total=Sum("quantity_available"))
        .values("total")[:1]
    )

    return queryset.annotate(
        available_stock=Coalesce(
            Subquery(
                per_product_stock,
                output_field=DecimalField(max_digits=12, decimal_places=3),
            ),
            Value(ZERO_QTY),
            output_field=DecimalField(max_digits=12, decimal_places=3),
        )
    )


# ---------------------------------------------------------------------------
# Reservation
# ---------------------------------------------------------------------------
def _default_hold_expiry():
    minutes = getattr(settings, "STOCK_RESERVATION_MINUTES", 45)
    return timezone.now() + timedelta(minutes=minutes)


#: Sentinel so callers can pass ``expires_at=None`` to mean "hold indefinitely"
#: (cash-on-delivery orders are committed at placement and never time out).
_UNSET = object()


def reserve_stock(
    *,
    order,
    variant,
    quantity: Decimal | int,
    order_item=None,
    performed_by=None,
    expires_at=_UNSET,
) -> list[StockReservation]:
    """Hold `quantity` of `variant` for `order`, splitting across batches FEFO.

    Must be called inside an outer transaction (checkout opens one) so that a
    failure on any line rolls back every line's allocation.

    Pass ``expires_at=None`` for holds that must never auto-release.

    Raises InsufficientStockError when the full quantity cannot be satisfied.
    """
    wanted = to_quantity(quantity)
    if wanted <= 0:
        raise ValueError("Reservation quantity must be positive.")

    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("reserve_stock() must run inside a transaction.")

    if expires_at is _UNSET:
        expires_at = _default_hold_expiry()
    as_of = timezone.localdate()
    cutoff = as_of + timedelta(days=_min_shelf_life_days())

    # Lock candidate rows in FEFO order. Ordering is deterministic so concurrent
    # checkouts acquire locks in the same sequence and cannot deadlock.
    locked_batches = (
        InventoryBatch.objects.select_for_update()
        .filter(
            variant=variant,
            status=BatchStatus.ACTIVE,
            quantity_available__gt=0,
        )
        .filter(Q(expiry_date__isnull=True) | Q(expiry_date__gte=cutoff))
        .fefo()
    )

    reservations: list[StockReservation] = []
    remaining = wanted
    allocated = ZERO_QTY

    for batch in locked_batches:
        if remaining <= 0:
            break

        # batch.quantity_available is the post-lock value: safe to decrement.
        take = min(batch.quantity_available, remaining)
        if take <= 0:
            continue

        batch.quantity_available -= take
        batch.quantity_reserved += take
        # The batch stays ACTIVE even at zero available: reserved units are
        # still physically on the shelf and must remain shippable. DEPLETED is
        # only set once they actually leave in consume_reservations().
        batch.save(update_fields=["quantity_available", "quantity_reserved", "updated_at"])

        reservation = StockReservation.objects.create(
            order=order,
            order_item=order_item,
            batch=batch,
            variant_id=batch.variant_id,
            quantity=take,
            status=StockReservation.Status.HELD,
            expires_at=expires_at,
        )
        StockMovement.record(
            batch=batch,
            movement_type=StockMovement.Type.RESERVE,
            quantity=take,
            available_delta=-take,
            reserved_delta=take,
            reference_type="order",
            reference_id=getattr(order, "pk", "") or "",
            performed_by=performed_by,
            note=f"Reserved for order {getattr(order, 'number', order.pk)}",
        )

        reservations.append(reservation)
        remaining -= take
        allocated += take

    if remaining > 0:
        # Roll back to the true availability figure for the error message: what
        # we managed to grab is all that existed.
        logger.warning(
            "Insufficient stock for variant %s: wanted %s, could allocate %s",
            getattr(variant, "sku", variant),
            wanted,
            allocated,
        )
        raise InsufficientStockError(variant, wanted, allocated)

    return reservations


@transaction.atomic
def release_reservations(
    *,
    order,
    reason: str = "Order cancelled",
    performed_by=None,
    reservations: Sequence[StockReservation] | None = None,
) -> Decimal:
    """Return held stock to the available pool. Returns the quantity released.

    Idempotent: reservations already consumed or released are skipped.
    """
    queryset = (
        reservations
        if reservations is not None
        else StockReservation.objects.select_for_update().filter(
            order=order, status=StockReservation.Status.HELD
        )
    )

    released_total = ZERO_QTY
    now = timezone.now()

    for reservation in queryset:
        if reservation.status != StockReservation.Status.HELD:
            continue

        batch = InventoryBatch.objects.select_for_update().get(pk=reservation.batch_id)
        quantity = reservation.quantity

        # Never push reserved below zero, even if data drifted.
        give_back = min(quantity, batch.quantity_reserved)
        batch.quantity_reserved -= give_back
        batch.quantity_available += give_back
        if batch.status == BatchStatus.DEPLETED and batch.quantity_available > 0:
            batch.status = BatchStatus.ACTIVE
        batch.save(
            update_fields=["quantity_available", "quantity_reserved", "status", "updated_at"]
        )

        StockMovement.record(
            batch=batch,
            movement_type=StockMovement.Type.RELEASE,
            quantity=give_back,
            available_delta=give_back,
            reserved_delta=-give_back,
            reference_type="order",
            reference_id=getattr(order, "pk", "") or "",
            performed_by=performed_by,
            note=reason,
        )

        reservation.status = StockReservation.Status.RELEASED
        reservation.resolved_at = now
        reservation.save(update_fields=["status", "resolved_at", "updated_at"])
        released_total += give_back

    if released_total:
        logger.info(
            "Released %s units back to stock for order %s (%s)",
            released_total,
            getattr(order, "number", getattr(order, "pk", "?")),
            reason,
        )
    return released_total


@transaction.atomic
def consume_reservations(*, order, performed_by=None, note: str = "") -> Decimal:
    """Convert held stock into shipped stock. Called when an order is dispatched."""
    held = StockReservation.objects.select_for_update().filter(
        order=order, status=StockReservation.Status.HELD
    )

    consumed_total = ZERO_QTY
    now = timezone.now()

    for reservation in held:
        batch = InventoryBatch.objects.select_for_update().get(pk=reservation.batch_id)
        quantity = min(reservation.quantity, batch.quantity_reserved)

        batch.quantity_reserved -= quantity
        if batch.quantity_available <= 0 and batch.quantity_reserved <= 0:
            batch.status = BatchStatus.DEPLETED
        batch.save(
            update_fields=["quantity_reserved", "status", "updated_at"]
        )

        StockMovement.record(
            batch=batch,
            movement_type=StockMovement.Type.SHIP,
            quantity=quantity,
            available_delta=ZERO_QTY,
            reserved_delta=-quantity,
            reference_type="order",
            reference_id=getattr(order, "pk", "") or "",
            performed_by=performed_by,
            note=note or f"Dispatched order {getattr(order, 'number', order.pk)}",
        )

        reservation.status = StockReservation.Status.CONSUMED
        reservation.resolved_at = now
        reservation.save(update_fields=["status", "resolved_at", "updated_at"])
        consumed_total += quantity

    return consumed_total


@transaction.atomic
def return_to_stock(*, order, performed_by=None, note: str = "Customer return") -> Decimal:
    """Put shipped goods back after a return, as a fresh movement on the batch."""
    consumed = StockReservation.objects.select_for_update().filter(
        order=order, status=StockReservation.Status.CONSUMED
    )

    returned_total = ZERO_QTY
    for reservation in consumed:
        batch = InventoryBatch.objects.select_for_update().get(pk=reservation.batch_id)

        # Expired goods coming back are written off, not resold.
        if batch.is_expired:
            continue

        batch.quantity_available += reservation.quantity
        if batch.status == BatchStatus.DEPLETED:
            batch.status = BatchStatus.ACTIVE
        batch.save(update_fields=["quantity_available", "status", "updated_at"])

        StockMovement.record(
            batch=batch,
            movement_type=StockMovement.Type.RETURN,
            quantity=reservation.quantity,
            available_delta=reservation.quantity,
            reserved_delta=ZERO_QTY,
            reference_type="order",
            reference_id=getattr(order, "pk", "") or "",
            performed_by=performed_by,
            note=note,
        )
        returned_total += reservation.quantity

    return returned_total


# ---------------------------------------------------------------------------
# Goods-in and corrections
# ---------------------------------------------------------------------------
@transaction.atomic
def receive_stock(
    *,
    variant,
    quantity: Decimal | int,
    batch_number: str,
    expiry_date: date | None = None,
    production_date: date | None = None,
    received_date: date | None = None,
    unit_cost: Decimal | None = None,
    supplier: str = "",
    storage_location: str = "",
    notes: str = "",
    performed_by=None,
) -> InventoryBatch:
    """Book a new batch of stock in, or top up an existing batch number."""
    amount = to_quantity(quantity)
    if amount <= 0:
        raise ValueError("Received quantity must be positive.")

    received_date = received_date or timezone.localdate()
    if expiry_date is None and variant.product.is_perishable:
        expiry_date = received_date + timedelta(days=variant.product.default_shelf_life_days)

    batch, created = InventoryBatch.objects.select_for_update().get_or_create(
        variant=variant,
        batch_number=batch_number,
        defaults={
            "quantity_received": amount,
            "quantity_available": amount,
            "expiry_date": expiry_date,
            "production_date": production_date,
            "received_date": received_date,
            "unit_cost": unit_cost,
            "supplier": supplier,
            "storage_location": storage_location,
            "notes": notes,
        },
    )

    if not created:
        batch.quantity_received += amount
        batch.quantity_available += amount
        if batch.status in {BatchStatus.DEPLETED, BatchStatus.EXPIRED}:
            batch.status = BatchStatus.ACTIVE
        batch.save(
            update_fields=[
                "quantity_received",
                "quantity_available",
                "status",
                "updated_at",
            ]
        )

    StockMovement.record(
        batch=batch,
        movement_type=StockMovement.Type.RECEIVE,
        quantity=amount,
        available_delta=amount,
        reserved_delta=ZERO_QTY,
        reference_type="goods_in",
        reference_id=batch.batch_number,
        performed_by=performed_by,
        note=notes or ("New batch received" if created else "Batch topped up"),
    )
    return batch


@transaction.atomic
def adjust_batch(
    *,
    batch: InventoryBatch,
    new_available: Decimal,
    reason: str,
    performed_by=None,
) -> InventoryBatch:
    """Correct a batch's available quantity after a stock count or spoilage."""
    target = to_quantity(new_available)
    if target < 0:
        raise ValueError("Available quantity cannot be negative.")

    locked = InventoryBatch.objects.select_for_update().get(pk=batch.pk)
    delta = target - locked.quantity_available
    if delta == 0:
        return locked

    locked.quantity_available = target
    # Keep quantity_received consistent when counting stock upward.
    if locked.quantity_available + locked.quantity_reserved > locked.quantity_received:
        locked.quantity_received = locked.quantity_available + locked.quantity_reserved
    if target > 0 and locked.status == BatchStatus.DEPLETED:
        locked.status = BatchStatus.ACTIVE
    locked.save(
        update_fields=["quantity_available", "quantity_received", "status", "updated_at"]
    )

    StockMovement.record(
        batch=locked,
        movement_type=StockMovement.Type.ADJUST,
        quantity=abs(delta),
        available_delta=delta,
        reserved_delta=ZERO_QTY,
        reference_type="adjustment",
        performed_by=performed_by,
        note=reason,
    )
    return locked


@transaction.atomic
def expire_batches(*, as_of: date | None = None, performed_by=None) -> int:
    """Write off every batch past its expiry date. Returns the number affected.

    Available units are zeroed so they can never be sold. Reserved units are
    left alone: they belong to orders already being packed and are handled by
    the operations team, who are alerted separately.
    """
    as_of = as_of or timezone.localdate()
    stale = (
        InventoryBatch.objects.select_for_update()
        .filter(expiry_date__isnull=False, expiry_date__lt=as_of)
        .exclude(status=BatchStatus.EXPIRED)
    )

    affected = 0
    for batch in stale:
        written_off = batch.quantity_available
        batch.quantity_available = ZERO_QTY
        batch.status = BatchStatus.EXPIRED
        batch.save(update_fields=["quantity_available", "status", "updated_at"])

        if written_off > 0:
            StockMovement.record(
                batch=batch,
                movement_type=StockMovement.Type.EXPIRE,
                quantity=written_off,
                available_delta=-written_off,
                reserved_delta=ZERO_QTY,
                reference_type="expiry",
                reference_id=str(as_of),
                performed_by=performed_by,
                note=f"Expired on {batch.expiry_date}",
            )
        affected += 1

    if affected:
        logger.info("Expired %s inventory batches as of %s", affected, as_of)
    return affected


# ---------------------------------------------------------------------------
# Operational reporting
# ---------------------------------------------------------------------------
def low_stock_variants(threshold: int | None = None):
    """Active variants at or below the low-stock threshold, lowest first."""
    from apps.products.models import ProductVariant

    threshold = threshold if threshold is not None else settings.LOW_STOCK_THRESHOLD
    variants = list(
        ProductVariant.objects.sellable().select_related("product", "product__category")
    )
    stock = available_quantity_map(variants)

    rows = [
        {"variant": variant, "available": stock.get(variant.pk, ZERO_QTY)}
        for variant in variants
        if stock.get(variant.pk, ZERO_QTY) <= threshold
    ]
    return sorted(rows, key=lambda row: row["available"])


def expiring_batches(days: int | None = None):
    """Batches expiring within `days`, soonest first."""
    days = days if days is not None else settings.EXPIRY_ALERT_DAYS
    return (
        InventoryBatch.objects.expiring_within(days)
        .select_related("variant", "variant__product")
        .order_by("expiry_date")
    )
