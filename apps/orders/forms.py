"""Checkout forms.

The form only ever collects choices (which address, which slot, which payment
method). Every amount of money is computed server-side in
``apps.cart.services.quote_cart``.
"""

from __future__ import annotations

from django import forms
from django.conf import settings
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.delivery.models import DeliverySlot
from apps.delivery.services import cod_available_for, earliest_delivery_date, resolve_zone_for_address
from apps.payments.constants import PaymentMethod


def enabled_payment_methods() -> list[tuple[str, str]]:
    """Payment choices the deployment has switched on."""
    enabled = set(getattr(settings, "ENABLED_PAYMENT_PROVIDERS", ["cod"]))
    return [
        (value, label) for value, label in PaymentMethod.choices if value in enabled
    ]


class CheckoutForm(forms.Form):
    address = forms.ModelChoiceField(
        queryset=None,
        label=_("Delivery address"),
        empty_label=None,
        widget=forms.RadioSelect,
        error_messages={"required": _("Choose where we should deliver.")},
    )
    delivery_slot = forms.ModelChoiceField(
        queryset=DeliverySlot.objects.none(),
        label=_("Preferred time"),
        required=False,
        empty_label=_("Any time"),
    )
    requested_delivery_date = forms.DateField(
        label=_("Preferred date"),
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    payment_method = forms.ChoiceField(
        label=_("Payment"),
        choices=(),
        widget=forms.RadioSelect,
        error_messages={"required": _("Choose how you would like to pay.")},
    )
    customer_note = forms.CharField(
        label=_("Order note"),
        required=False,
        max_length=500,
        widget=forms.Textarea(
            attrs={"rows": 3, "placeholder": _("Anything the rider should know?")}
        ),
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

        addresses = user.addresses.select_related("delivery_zone").all()
        self.fields["address"].queryset = addresses
        default = addresses.filter(is_default=True).first()
        if default is not None:
            self.fields["address"].initial = default.pk

        methods = enabled_payment_methods()
        self.fields["payment_method"].choices = methods
        if methods:
            self.fields["payment_method"].initial = methods[0][0]

        # Slots are filtered down once we know the address's zone.
        self.fields["delivery_slot"].queryset = DeliverySlot.objects.filter(
            is_active=True
        ).select_related("zone")

    def clean_requested_delivery_date(self):
        value = self.cleaned_data.get("requested_delivery_date")
        if value and value < timezone.localdate():
            raise forms.ValidationError(_("Pick a date from today onwards."))
        return value

    def clean(self):
        cleaned = super().clean()
        address = cleaned.get("address")
        if address is None:
            return cleaned

        zone = address.delivery_zone or resolve_zone_for_address(address)
        self.zone = zone

        method = cleaned.get("payment_method")
        if method == PaymentMethod.COD and not cod_available_for(zone):
            self.add_error(
                "payment_method",
                _("Cash on delivery is not available for this address."),
            )

        slot = cleaned.get("delivery_slot")
        if slot is not None and slot.zone_id not in (None, getattr(zone, "pk", None)):
            self.add_error("delivery_slot", _("That time is not served in this area."))

        requested = cleaned.get("requested_delivery_date")
        earliest = earliest_delivery_date(zone)
        if requested and requested < earliest:
            self.add_error(
                "requested_delivery_date",
                _("The earliest we can deliver here is %(date)s.")
                % {"date": earliest.strftime("%d %b %Y")},
            )
        return cleaned


class OrderCancelForm(forms.Form):
    reason = forms.CharField(
        label=_("Why are you cancelling?"),
        required=False,
        max_length=200,
        widget=forms.TextInput(attrs={"placeholder": _("Optional")}),
    )


class OrderStatusForm(forms.Form):
    """Staff dashboard status change."""

    status = forms.ChoiceField(label=_("Move to"), choices=())
    note = forms.CharField(label=_("Note"), required=False, max_length=300)

    def __init__(self, *args, order=None, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.orders.services import next_statuses

        self.order = order
        self.fields["status"].choices = next_statuses(order) if order else []
