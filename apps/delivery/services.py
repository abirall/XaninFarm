"""Delivery pricing and zone resolution.

Every number produced here is derived from server-side data (the saved
address, the zone table, the site settings). Nothing is taken from the
request body.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from django.db.models import Q
from django.utils import timezone

from apps.core.models import SiteSetting
from apps.core.utils import ZERO, money
from apps.delivery.models import DeliverySlot, DeliveryZone


def resolve_zone_for_address(address) -> DeliveryZone | None:
    """Find the zone that serves an address.

    Matching is narrowest-first: postcode, then area/thana, then district.
    Falls back to the zone flagged ``is_default`` so checkout can still price
    an order for an address outside the mapped areas.
    """
    zones = list(DeliveryZone.objects.active())
    if not zones:
        return None

    postcode = (getattr(address, "postcode", "") or "").strip().casefold()
    area = (getattr(address, "area", "") or "").strip().casefold()
    district = (getattr(address, "district", "") or "").strip().casefold()

    if postcode:
        for zone in zones:
            if postcode in zone.postcode_set:
                return zone
    if area:
        for zone in zones:
            if area in zone.area_set:
                return zone
    if district:
        for zone in zones:
            if district in zone.district_set:
                return zone

    return next((zone for zone in zones if zone.is_default), None)


def delivery_fee_for(zone: DeliveryZone | None, subtotal: Decimal) -> Decimal:
    """Delivery charge for a subtotal, honouring the free-delivery threshold."""
    settings_row = SiteSetting.get_solo()
    subtotal = money(subtotal)

    if zone is None:
        fee = settings_row.default_delivery_fee
        threshold = settings_row.free_delivery_threshold
    else:
        fee = zone.delivery_fee
        threshold = (
            zone.free_delivery_threshold
            if zone.free_delivery_threshold is not None
            else settings_row.free_delivery_threshold
        )

    if threshold > ZERO and subtotal >= threshold:
        return ZERO
    return money(fee)


def free_delivery_threshold_for(zone: DeliveryZone | None) -> Decimal:
    """The subtotal at which delivery becomes free (0 means never)."""
    settings_row = SiteSetting.get_solo()
    if zone is not None and zone.free_delivery_threshold is not None:
        return money(zone.free_delivery_threshold)
    return money(settings_row.free_delivery_threshold)


def min_order_amount_for(zone: DeliveryZone | None) -> Decimal:
    """The smallest subtotal this zone will accept."""
    settings_row = SiteSetting.get_solo()
    zone_minimum = money(zone.min_order_amount) if zone is not None else ZERO
    return max(zone_minimum, money(settings_row.min_order_amount))


def cod_available_for(zone: DeliveryZone | None) -> bool:
    """Whether cash on delivery may be offered for this zone."""
    if not SiteSetting.get_solo().cod_enabled:
        return False
    return zone.cod_available if zone is not None else True


def available_slots(zone: DeliveryZone | None, *, for_date: date | None = None):
    """Slots bookable for a date, with past-cutoff windows removed."""
    slots = DeliverySlot.objects.filter(is_active=True).select_related("zone")
    # Slots with no zone are offered everywhere.
    slots = slots.filter(Q(zone=zone) | Q(zone__isnull=True)) if zone else slots.filter(
        zone__isnull=True
    )

    for_date = for_date or earliest_delivery_date(zone)
    now = timezone.localtime()
    if for_date > now.date():
        return list(slots)

    bookable = []
    for slot in slots:
        opens = timezone.make_aware(
            datetime.combine(for_date, slot.start_time), now.tzinfo
        )
        if now <= opens - timedelta(hours=slot.cutoff_hours):
            bookable.append(slot)
    return bookable


def earliest_delivery_date(zone: DeliveryZone | None) -> date:
    days = zone.estimated_days_min if zone is not None else 1
    return timezone.localdate() + timedelta(days=days)


def latest_delivery_date(zone: DeliveryZone | None) -> date:
    days = zone.estimated_days_max if zone is not None else 2
    return timezone.localdate() + timedelta(days=days)


def delivery_estimate_label(zone: DeliveryZone | None) -> str:
    if zone is None:
        return "1-2 days"
    return zone.estimate_label
