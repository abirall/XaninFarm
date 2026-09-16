"""Registration, login, profile and address forms."""

from __future__ import annotations

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm, PasswordResetForm, SetPasswordForm
from django.utils.translation import gettext_lazy as _

from apps.accounts.models import Address
from apps.core.validators import normalize_bd_phone

User = get_user_model()


class RegistrationForm(forms.ModelForm):
    """Create a customer account."""

    password1 = forms.CharField(
        label=_("Password"),
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        help_text=_("At least 8 characters. Avoid common or all-numeric passwords."),
    )
    password2 = forms.CharField(
        label=_("Confirm password"),
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    accept_terms = forms.BooleanField(
        label=_("I agree to the Terms of Service and Privacy Policy"),
        required=True,
        error_messages={"required": _("Please accept the terms to create an account.")},
    )

    class Meta:
        model = User
        fields = ["full_name", "email", "phone", "marketing_opt_in"]
        widgets = {
            "full_name": forms.TextInput(
                attrs={"autocomplete": "name", "placeholder": "Rahim Uddin"}
            ),
            "email": forms.EmailInput(
                attrs={"autocomplete": "email", "placeholder": "you@example.com"}
            ),
            "phone": forms.TextInput(
                attrs={"autocomplete": "tel", "placeholder": "01712345678", "inputmode": "tel"}
            ),
        }
        labels = {"marketing_opt_in": _("Send me seasonal offers from the farm")}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["phone"].required = True
        self.fields["email"].required = True

    def clean_email(self) -> str:
        email = self.cleaned_data["email"].lower().strip()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(
                _("An account with this email already exists. Try signing in instead.")
            )
        return email

    def clean_phone(self) -> str:
        phone = normalize_bd_phone(self.cleaned_data.get("phone"))
        if not phone:
            raise forms.ValidationError(_("Enter your mobile number."))
        if User.objects.filter(phone=phone).exists():
            raise forms.ValidationError(_("An account with this mobile number already exists."))
        return phone

    def clean(self):
        cleaned = super().clean()
        password1 = cleaned.get("password1")
        password2 = cleaned.get("password2")
        if password1 and password2 and password1 != password2:
            self.add_error("password2", _("The two password fields did not match."))
        return cleaned

    def _post_clean(self):
        super()._post_clean()
        # Validate the password against AUTH_PASSWORD_VALIDATORS with the user
        # instance available, so similarity-to-email checks work.
        password = self.cleaned_data.get("password1")
        if password:
            from django.contrib.auth.password_validation import validate_password

            try:
                validate_password(password, self.instance)
            except forms.ValidationError as error:
                self.add_error("password1", error)

    def save(self, commit: bool = True) -> User:
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password1"])
        if commit:
            user.save()
        return user


class EmailOrPhoneLoginForm(AuthenticationForm):
    """Login accepting either an email address or a mobile number."""

    username = forms.CharField(
        label=_("Email or mobile"),
        widget=forms.TextInput(
            attrs={"autofocus": True, "autocomplete": "username", "placeholder": "you@example.com"}
        ),
    )
    password = forms.CharField(
        label=_("Password"),
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
    )

    error_messages = {
        **AuthenticationForm.error_messages,
        "invalid_login": _("Those credentials do not match an account. Please try again."),
        "inactive": _("This account has been deactivated. Contact support for help."),
    }


class ProfileForm(forms.ModelForm):
    """Edit the signed-in customer's own details."""

    class Meta:
        model = User
        fields = ["full_name", "phone", "marketing_opt_in"]
        widgets = {
            "full_name": forms.TextInput(attrs={"autocomplete": "name"}),
            "phone": forms.TextInput(attrs={"autocomplete": "tel", "inputmode": "tel"}),
        }
        labels = {"marketing_opt_in": _("Send me seasonal offers from the farm")}

    def clean_phone(self) -> str | None:
        phone = normalize_bd_phone(self.cleaned_data.get("phone"))
        if not phone:
            return None
        clash = User.objects.filter(phone=phone).exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError(_("Another account already uses this mobile number."))
        return phone


class AddressForm(forms.ModelForm):
    """Create or edit a delivery address."""

    set_as_default = forms.BooleanField(
        label=_("Make this my default delivery address"), required=False
    )

    class Meta:
        model = Address
        fields = [
            "label",
            "recipient_name",
            "phone",
            "alternate_phone",
            "division",
            "district",
            "area",
            "address_line",
            "postcode",
            "delivery_note",
        ]
        widgets = {
            "recipient_name": forms.TextInput(attrs={"autocomplete": "name"}),
            "phone": forms.TextInput(attrs={"autocomplete": "tel", "inputmode": "tel"}),
            "alternate_phone": forms.TextInput(attrs={"inputmode": "tel"}),
            "district": forms.TextInput(
                attrs={"autocomplete": "address-level2", "placeholder": "Dhaka"}
            ),
            "area": forms.TextInput(
                attrs={"autocomplete": "address-level3", "placeholder": "Dhanmondi"}
            ),
            "address_line": forms.Textarea(
                attrs={
                    "rows": 2,
                    "autocomplete": "street-address",
                    "placeholder": "House 12, Road 5, Block B",
                }
            ),
            "postcode": forms.TextInput(
                attrs={"autocomplete": "postal-code", "inputmode": "numeric"}
            ),
            "delivery_note": forms.TextInput(
                attrs={"placeholder": "Beside the pharmacy, 3rd floor"}
            ),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        if self.instance.pk and self.instance.is_default:
            self.fields["set_as_default"].initial = True
            self.fields["set_as_default"].disabled = True
            self.fields["set_as_default"].help_text = _("This is already your default address.")

    def clean_phone(self) -> str:
        return normalize_bd_phone(self.cleaned_data.get("phone"))

    def save(self, commit: bool = True) -> Address:
        address = super().save(commit=False)
        if self.user is not None:
            address.user = self.user

        # Resolve the delivery zone from the area/district so checkout can
        # price delivery without asking the customer to pick a zone.
        from apps.delivery.services import resolve_zone_for_address

        address.delivery_zone = resolve_zone_for_address(address)

        if commit:
            address.save()
            if self.cleaned_data.get("set_as_default"):
                address.make_default()
        return address


class BrandedPasswordResetForm(PasswordResetForm):
    """Password reset that renders the XaninFarm email template."""

    email = forms.EmailField(
        label=_("Email"),
        max_length=254,
        widget=forms.EmailInput(attrs={"autocomplete": "email", "autofocus": True}),
    )


class BrandedSetPasswordForm(SetPasswordForm):
    """Choose a new password after following a reset link."""

    new_password1 = forms.CharField(
        label=_("New password"),
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password", "autofocus": True}),
    )
    new_password2 = forms.CharField(
        label=_("Confirm new password"),
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
