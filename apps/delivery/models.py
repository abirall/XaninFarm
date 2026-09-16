"""Delivery zones, slots and riders.

Delivery pricing is a server-side decision: the zone is resolved from the
saved address, and the fee is read from the zone. The client never supplies
a delivery fee.
"""

from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel
from apps.core.utils import MONEY_FIELD, unique_slugify
from apps.core.validators import validate_bd_phone


class DeliveryZoneQuerySet(models.QuerySet):
    def active(self):
        return self.filter(is_active=True)


class DeliveryZone(TimeStampedModel):
    """A priced delivery area, matched against an address's area/district."""

    name = models.CharField(_("name"), max_length=120)
    slug = models.SlugField(_("slug"), max_length=140, unique=True, blank=True)
    description = models.CharField(_("description"), max_length=220, blank=True)

    districts = models.TextField(
        _("districts"),
        blank=True,
        help_text=_("Comma separated, e.g. Dhaka, Gazipur. Matched case-insensitively."),
    )
    areas = models.TextField(
        _("areas / thanas"),
        blank=True,
        help_text=_("Comma separated, e.g. Dhanmondi, Gulshan. Checked before districts."),
    )
    postcodes = models.TextField(
        _("postcodes"), blank=True, help_text=_("Comma separated. Checked first when present.")
    )

    delivery_fee = models.DecimalField(
        _("delivery fee"),
        default=Decimal("60.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
        **MONEY_FIELD,
    )
    free_delivery_threshold = models.DecimalField(
        _("free delivery threshold"),
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.00"))],
        help_text=_("Leave empty to use the site-wide threshold. Set 0 to never ship free."),
        **MONEY_FIELD,
    )
    min_order_amount = models.DecimalField(
        _("minimum order amount"),
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
        **MONEY_FIELD,
    )

    estimated_days_min = models.PositiveSmallIntegerField(_("earliest delivery (days)"), default=1)
    estimated_days_max = models.PositiveSmallIntegerField(_("latest delivery (days)"), default=2)

    cod_available = models.BooleanField(_("cash on delivery available"), default=True)
    is_active = models.BooleanField(_("active"), default=True, db_index=True)
    is_default = models.BooleanField(
        _("fallback zone"),
        default=False,
        help_text=_("Used when no other zone matches the address."),
    )
    sort_order = models.PositiveIntegerField(_("sort order"), default=0)

    objects = DeliveryZoneQuerySet.as_manager()

    class Meta:
        ordering = ("sort_order", "name")
        verbose_name = _("delivery zone")
        verbose_name_plural = _("delivery zones")
        constraints = [
            models.UniqueConstraint(
                fields=["is_default"],
                condition=models.Q(is_default=True),
                name="only_one_default_delivery_zone",
            )
        ]

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slugify(self, self.name)
        super().save(*args, **kwargs)

    def clean(self):
        if self.estimated_days_max < self.estimated_days_min:
            raise ValidationError(
                {"estimated_days_max": _("Latest delivery cannot be before the earliest.")}
            )

    # --- Matching helpers -----------------------------------------------------
    @staticmethod
    def _tokens(raw: str) -> set[str]:
        return {part.strip().casefold() for part in (raw or "").split(",") if part.strip()}

    @property
    def district_set(self) -> set[str]:
        return self._tokens(self.districts)

    @property
    def area_set(self) -> set[str]:
        return self._tokens(self.areas)

    @property
    def postcode_set(self) -> set[str]:
        return self._tokens(self.postcodes)

    @property
    def estimate_label(self) -> str:
        if self.estimated_days_min == self.estimated_days_max:
            unit = "day" if self.estimated_days_min == 1 else "days"
            return f"{self.estimated_days_min} {unit}"
        return f"{self.estimated_days_min}-{self.estimated_days_max} days"


class DeliverySlot(TimeStampedModel):
    """A bookable delivery window."""

    zone = models.ForeignKey(
        DeliveryZone,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="slots",
        verbose_name=_("zone"),
        help_text=_("Leave empty to offer this slot in every zone."),
    )
    label = models.CharField(_("label"), max_length=80, help_text=_("e.g. Morning (8 AM - 11 AM)"))
    start_time = models.TimeField(_("window opens"))
    end_time = models.TimeField(_("window closes"))

    cutoff_hours = models.PositiveSmallIntegerField(
        _("cutoff (hours before)"),
        default=4,
        help_text=_("Orders must be placed this many hours before the window opens."),
    )
    max_orders_per_day = models.PositiveIntegerField(
        _("capacity per day"), default=0, help_text=_("0 means unlimited.")
    )

    is_active = models.BooleanField(_("active"), default=True, db_index=True)
    sort_order = models.PositiveIntegerField(_("sort order"), default=0)

    class Meta:
        ordering = ("sort_order", "start_time")
        verbose_name = _("delivery slot")
        verbose_name_plural = _("delivery slots")

    def __str__(self) -> str:
        return f"{self.label}{f' - {self.zone.name}' if self.zone_id else ''}"

    def clean(self):
        if self.end_time <= self.start_time:
            raise ValidationError({"end_time": _("The window must close after it opens.")})


class DeliveryPartner(TimeStampedModel):
    """A rider or courier who carries orders out."""

    class Vehicle(models.TextChoices):
        BIKE = "bike", _("Motorbike")
        VAN = "van", _("Van")
        CYCLE = "cycle", _("Bicycle")
        TRUCK = "truck", _("Refrigerated truck")

    name = models.CharField(_("name"), max_length=120)
    phone = models.CharField(_("phone"), max_length=20, validators=[validate_bd_phone])
    vehicle = models.CharField(
        _("vehicle"), max_length=10, choices=Vehicle.choices, default=Vehicle.BIKE
    )
    zones = models.ManyToManyField(
        DeliveryZone, blank=True, related_name="partners", verbose_name=_("zones covered")
    )
    user = models.OneToOneField(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="delivery_profile",
        verbose_name=_("linked staff account"),
    )
    is_active = models.BooleanField(_("active"), default=True, db_index=True)
    notes = models.CharField(_("notes"), max_length=220, blank=True)

    class Meta:
        ordering = ("name",)
        verbose_name = _("delivery partner")
        verbose_name_plural = _("delivery partners")

    def __str__(self) -> str:
        return f"{self.name} ({self.get_vehicle_display()})"
