"""Custom user and customer delivery addresses."""

from __future__ import annotations

from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models, transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.accounts.managers import UserManager
from apps.core.models import TimeStampedModel
from apps.core.validators import normalize_bd_phone, validate_bd_phone


class User(AbstractBaseUser, PermissionsMixin):
    """A XaninFarm account. Email is the login identifier."""

    email = models.EmailField(_("email address"), unique=True)
    full_name = models.CharField(_("full name"), max_length=150)
    phone = models.CharField(
        _("mobile number"),
        max_length=20,
        unique=True,
        null=True,
        blank=True,
        validators=[validate_bd_phone],
        help_text=_("Used for delivery coordination and order updates."),
    )

    is_active = models.BooleanField(_("active"), default=True)
    is_staff = models.BooleanField(
        _("staff status"),
        default=False,
        help_text=_("Designates whether the user can access the farm dashboard."),
    )
    email_verified = models.BooleanField(_("email verified"), default=False)
    marketing_opt_in = models.BooleanField(
        _("marketing emails"),
        default=False,
        help_text=_("Consent to receive offers and seasonal produce updates."),
    )

    date_joined = models.DateTimeField(_("date joined"), default=timezone.now)

    objects = UserManager()

    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS = ["full_name"]

    class Meta:
        verbose_name = _("user")
        verbose_name_plural = _("users")
        ordering = ("-date_joined",)
        indexes = [models.Index(fields=["email"]), models.Index(fields=["phone"])]

    def __str__(self) -> str:
        return self.email

    def save(self, *args, **kwargs):
        self.email = self.email.lower().strip()
        # Keep the unique phone column NULL rather than "" when unset.
        self.phone = normalize_bd_phone(self.phone) or None
        super().save(*args, **kwargs)

    def get_full_name(self) -> str:
        return self.full_name.strip() or self.email

    def get_short_name(self) -> str:
        return self.full_name.strip().split(" ")[0] if self.full_name.strip() else self.email

    @property
    def initials(self) -> str:
        parts = [part for part in self.full_name.strip().split(" ") if part]
        if not parts:
            return self.email[:1].upper()
        if len(parts) == 1:
            return parts[0][:2].upper()
        return f"{parts[0][0]}{parts[-1][0]}".upper()

    @property
    def default_address(self):
        return self.addresses.filter(is_default=True).first() or self.addresses.first()


class Address(TimeStampedModel):
    """A saved delivery address.

    Orders snapshot the address text at checkout, so editing or deleting an
    address never rewrites delivery history.
    """

    class Label(models.TextChoices):
        HOME = "home", _("Home")
        OFFICE = "office", _("Office")
        OTHER = "other", _("Other")

    class Division(models.TextChoices):
        DHAKA = "dhaka", _("Dhaka")
        CHATTOGRAM = "chattogram", _("Chattogram")
        KHULNA = "khulna", _("Khulna")
        RAJSHAHI = "rajshahi", _("Rajshahi")
        BARISHAL = "barishal", _("Barishal")
        SYLHET = "sylhet", _("Sylhet")
        RANGPUR = "rangpur", _("Rangpur")
        MYMENSINGH = "mymensingh", _("Mymensingh")

    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="addresses",
        verbose_name=_("user"),
    )
    label = models.CharField(
        _("label"), max_length=10, choices=Label.choices, default=Label.HOME
    )

    recipient_name = models.CharField(_("recipient name"), max_length=150)
    phone = models.CharField(_("phone"), max_length=20, validators=[validate_bd_phone])
    alternate_phone = models.CharField(
        _("alternate phone"), max_length=20, blank=True, validators=[validate_bd_phone]
    )

    division = models.CharField(
        _("division"), max_length=20, choices=Division.choices, default=Division.DHAKA
    )
    district = models.CharField(_("district"), max_length=80)
    area = models.CharField(_("area / thana"), max_length=120)
    address_line = models.CharField(
        _("street address"),
        max_length=255,
        help_text=_("House, road, block and any building name."),
    )
    postcode = models.CharField(_("postcode"), max_length=10, blank=True)
    delivery_note = models.CharField(
        _("delivery note"),
        max_length=200,
        blank=True,
        help_text=_("Landmark or instructions for the rider."),
    )

    # Resolved at checkout to price delivery. SET_NULL so retiring a zone never
    # deletes a customer's address.
    delivery_zone = models.ForeignKey(
        "delivery.DeliveryZone",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="addresses",
        verbose_name=_("delivery zone"),
    )

    is_default = models.BooleanField(_("default address"), default=False)

    class Meta:
        verbose_name = _("address")
        verbose_name_plural = _("addresses")
        ordering = ("-is_default", "-updated_at")
        constraints = [
            # At most one default address per customer.
            models.UniqueConstraint(
                fields=["user"],
                condition=models.Q(is_default=True),
                name="unique_default_address_per_user",
            )
        ]

    def __str__(self) -> str:
        return f"{self.recipient_name}, {self.area}, {self.district}"

    def save(self, *args, **kwargs):
        self.phone = normalize_bd_phone(self.phone)
        if self.alternate_phone:
            self.alternate_phone = normalize_bd_phone(self.alternate_phone)
        # A customer's first address becomes their default automatically.
        if not self.pk and not self.user.addresses.exists():
            self.is_default = True
        super().save(*args, **kwargs)

    def get_absolute_url(self) -> str:
        return reverse("accounts:address_edit", kwargs={"pk": self.pk})

    @transaction.atomic
    def make_default(self) -> None:
        """Promote this address, demoting any current default first.

        The demote-then-promote order matters: the unique constraint would
        otherwise reject two defaults mid-transaction.
        """
        type(self).objects.filter(user=self.user, is_default=True).exclude(pk=self.pk).update(
            is_default=False
        )
        if not self.is_default:
            self.is_default = True
            self.save(update_fields=["is_default", "updated_at"])

    @property
    def single_line(self) -> str:
        parts = [
            self.address_line,
            self.area,
            self.district,
            self.get_division_display(),
            self.postcode,
        ]
        return ", ".join(part for part in parts if part)

    def as_snapshot(self) -> dict:
        """Flatten to the fields Order copies at checkout."""
        return {
            "recipient_name": self.recipient_name,
            "phone": self.phone,
            "alternate_phone": self.alternate_phone,
            "division": self.get_division_display(),
            "district": self.district,
            "area": self.area,
            "address_line": self.address_line,
            "postcode": self.postcode,
            "delivery_note": self.delivery_note,
        }
