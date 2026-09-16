"""Staff forms for goods-in and stock corrections.

These collect intent only. Every quantity change is applied by
``apps.inventory.services``, inside a transaction with the batch row locked,
so the dashboard cannot drive a quantity negative or bypass a stock movement.
"""

from __future__ import annotations

from decimal import Decimal

from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.products.models import ProductVariant


class ReceiveStockForm(forms.Form):
    """Book a new batch in, or top up an existing batch number."""

    variant = forms.ModelChoiceField(
        queryset=ProductVariant.objects.none(),
        label=_("Product / pack"),
        empty_label=_("Choose a pack"),
    )
    batch_number = forms.CharField(
        label=_("Batch number"),
        max_length=60,
        widget=forms.TextInput(attrs={"placeholder": "MILK-2026-09-12-A"}),
        help_text=_("Reuse an existing number to top that batch up."),
    )
    quantity = forms.DecimalField(
        label=_("Quantity received"),
        max_digits=12,
        decimal_places=3,
        min_value=Decimal("0.001"),
    )
    production_date = forms.DateField(
        label=_("Production date"),
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    received_date = forms.DateField(
        label=_("Received date"),
        widget=forms.DateInput(attrs={"type": "date"}),
        initial=timezone.localdate,
    )
    expiry_date = forms.DateField(
        label=_("Expiry date"),
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
        help_text=_("Required for perishables. Left empty, the shelf life is applied."),
    )
    unit_cost = forms.DecimalField(
        label=_("Unit cost"),
        required=False,
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.00"),
    )
    supplier = forms.CharField(label=_("Supplier / farm unit"), max_length=140, required=False)
    storage_location = forms.CharField(
        label=_("Storage location"), max_length=80, required=False
    )
    notes = forms.CharField(
        label=_("Notes"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["variant"].queryset = ProductVariant.objects.filter(
            is_active=True
        ).select_related("product")

    def clean_received_date(self):
        received = self.cleaned_data["received_date"]
        if received > timezone.localdate():
            raise forms.ValidationError(_("Stock cannot be received in the future."))
        return received

    def clean(self):
        cleaned = super().clean()
        variant = cleaned.get("variant")
        expiry = cleaned.get("expiry_date")
        production = cleaned.get("production_date")
        received = cleaned.get("received_date")

        # Mirrors InventoryBatch.clean(), but reported against the form field
        # so the staff member sees it next to the input rather than as a 500.
        if variant is not None and expiry is None and variant.product.is_perishable:
            if not variant.product.default_shelf_life_days:
                self.add_error(
                    "expiry_date",
                    _("This is a perishable product and has no default shelf life set."),
                )

        if expiry and production and expiry <= production:
            self.add_error("expiry_date", _("Expiry must be after the production date."))

        if expiry and received and expiry < received:
            self.add_error("expiry_date", _("This batch would already be expired."))

        return cleaned


class AdjustBatchForm(forms.Form):
    """Correct a batch's available quantity after a count or spoilage."""

    new_available = forms.DecimalField(
        label=_("Counted available quantity"),
        max_digits=12,
        decimal_places=3,
        min_value=Decimal("0.000"),
    )
    reason = forms.CharField(
        label=_("Reason"),
        max_length=255,
        widget=forms.TextInput(attrs={"placeholder": "Stock count, spoilage, breakage..."}),
        help_text=_("Recorded against the stock movement for the audit trail."),
    )
