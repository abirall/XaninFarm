"""Customer authentication, profile and address-book views."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import (
    LoginView,
    LogoutView,
    PasswordChangeDoneView,
    PasswordChangeView,
    PasswordResetCompleteView,
    PasswordResetConfirmView,
    PasswordResetDoneView,
    PasswordResetView,
)
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, DeleteView, FormView, ListView, UpdateView

from apps.accounts.forms import (
    AddressForm,
    BrandedPasswordResetForm,
    BrandedSetPasswordForm,
    EmailOrPhoneLoginForm,
    ProfileForm,
    RegistrationForm,
)
from apps.accounts.models import Address


class RegisterView(FormView):
    """Create an account and sign the customer straight in."""

    template_name = "accounts/register.html"
    form_class = RegistrationForm

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect("accounts:profile")
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        user = form.save()
        # Explicit backend: several are configurable and login() needs one.
        login(self.request, user, backend="apps.accounts.backends.EmailOrPhoneBackend")

        from apps.notifications.tasks import send_welcome_email

        send_welcome_email.delay(user.pk)

        messages.success(
            self.request,
            _("Welcome to XaninFarm, %(name)s. Your account is ready.")
            % {"name": user.get_short_name()},
        )
        return redirect(self.get_success_url())

    def get_success_url(self) -> str:
        next_url = self.request.GET.get("next")
        # Only honour same-site relative redirects.
        if next_url and next_url.startswith("/") and not next_url.startswith("//"):
            return next_url
        return str(reverse_lazy("accounts:profile"))


class BrandedLoginView(LoginView):
    template_name = "accounts/login.html"
    authentication_form = EmailOrPhoneLoginForm
    redirect_authenticated_user = True

    def form_valid(self, form):
        messages.success(
            self.request,
            _("Welcome back, %(name)s.") % {"name": form.get_user().get_short_name()},
        )
        return super().form_valid(form)


class BrandedLogoutView(LogoutView):
    next_page = reverse_lazy("core:home")


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------
@login_required
def profile(request) -> HttpResponse:
    """Account home: recent orders, default address, saved items."""
    from apps.cart.models import WishlistItem
    from apps.orders.models import Order

    # `for_user` excludes drafts, so an abandoned checkout never shows up as
    # an order. Payment state lives on the order itself, so the ledger does
    # not need joining here.
    orders = Order.objects.for_user(request.user)
    recent_orders = orders.prefetch_related("items").order_by("-placed_at")[:5]

    context = {
        "recent_orders": recent_orders,
        "order_count": orders.count(),
        "address_count": request.user.addresses.count(),
        "default_address": request.user.default_address,
        "wishlist_count": WishlistItem.objects.filter(wishlist__user=request.user).count(),
    }
    return render(request, "accounts/profile.html", context)


class ProfileEditView(LoginRequiredMixin, UpdateView):
    template_name = "accounts/profile_edit.html"
    form_class = ProfileForm
    success_url = reverse_lazy("accounts:profile")

    def get_object(self, queryset=None):
        # Always the requesting user - never a pk from the URL.
        return self.request.user

    def form_valid(self, form):
        messages.success(self.request, _("Your details have been updated."))
        return super().form_valid(form)


# ---------------------------------------------------------------------------
# Address book
# ---------------------------------------------------------------------------
class AddressListView(LoginRequiredMixin, ListView):
    template_name = "accounts/address_list.html"
    context_object_name = "addresses"

    def get_queryset(self):
        return self.request.user.addresses.select_related("delivery_zone")


class AddressCreateView(LoginRequiredMixin, CreateView):
    template_name = "accounts/address_form.html"
    form_class = AddressForm
    success_url = reverse_lazy("accounts:address_list")

    def get_form_kwargs(self) -> dict:
        return {**super().get_form_kwargs(), "user": self.request.user}

    def form_valid(self, form):
        messages.success(self.request, _("Address saved."))
        return super().form_valid(form)


class AddressUpdateView(LoginRequiredMixin, UpdateView):
    template_name = "accounts/address_form.html"
    form_class = AddressForm
    success_url = reverse_lazy("accounts:address_list")

    def get_queryset(self):
        # Ownership check: another customer's address is a 404.
        return self.request.user.addresses.all()

    def get_form_kwargs(self) -> dict:
        return {**super().get_form_kwargs(), "user": self.request.user}

    def form_valid(self, form):
        messages.success(self.request, _("Address updated."))
        return super().form_valid(form)


class AddressDeleteView(LoginRequiredMixin, DeleteView):
    template_name = "accounts/address_confirm_delete.html"
    success_url = reverse_lazy("accounts:address_list")

    def get_queryset(self):
        return self.request.user.addresses.all()

    def form_valid(self, form):
        address = self.get_object()
        was_default = address.is_default
        response = super().form_valid(form)
        # Promote another address so the customer always has a default.
        if was_default:
            replacement = self.request.user.addresses.first()
            if replacement:
                replacement.make_default()
        messages.success(self.request, _("Address removed."))
        return response


@login_required
@require_POST
def address_make_default(request, pk: int) -> HttpResponse:
    address = get_object_or_404(request.user.addresses, pk=pk)
    address.make_default()
    messages.success(request, _("Default delivery address updated."))
    return redirect("accounts:address_list")


# ---------------------------------------------------------------------------
# Password management
# ---------------------------------------------------------------------------
class BrandedPasswordChangeView(LoginRequiredMixin, PasswordChangeView):
    template_name = "accounts/password_change.html"
    success_url = reverse_lazy("accounts:password_change_done")


class BrandedPasswordChangeDoneView(LoginRequiredMixin, PasswordChangeDoneView):
    template_name = "accounts/password_change_done.html"


class BrandedPasswordResetView(PasswordResetView):
    template_name = "accounts/password_reset.html"
    form_class = BrandedPasswordResetForm
    email_template_name = "emails/password_reset.txt"
    html_email_template_name = "emails/password_reset.html"
    subject_template_name = "emails/password_reset_subject.txt"
    success_url = reverse_lazy("accounts:password_reset_done")

    def form_valid(self, form):
        """Give the reset email the same brand context as every other message.

        Django's own view only supplies uid/token/domain, so without this the
        shared emails/base.html header would render with an empty brand name
        and no support contact details. Set here rather than as a class
        attribute because it reads the SiteSetting row from the database.
        """
        from apps.notifications.emails import base_context

        self.extra_email_context = base_context()
        return super().form_valid(form)


class BrandedPasswordResetDoneView(PasswordResetDoneView):
    template_name = "accounts/password_reset_done.html"


class BrandedPasswordResetConfirmView(PasswordResetConfirmView):
    template_name = "accounts/password_reset_confirm.html"
    form_class = BrandedSetPasswordForm
    success_url = reverse_lazy("accounts:password_reset_complete")


class BrandedPasswordResetCompleteView(PasswordResetCompleteView):
    template_name = "accounts/password_reset_complete.html"


# ---------------------------------------------------------------------------
# Wishlist (lives under /account/ for discoverability)
# ---------------------------------------------------------------------------
@login_required
def wishlist(request) -> HttpResponse:
    from apps.cart.models import Wishlist, WishlistItem
    from apps.inventory.services import annotate_product_stock
    from apps.products.models import Product

    wishlist_obj, _created = Wishlist.objects.get_or_create(user=request.user)
    saved_order = list(
        WishlistItem.objects.filter(wishlist=wishlist_obj)
        .order_by("-created_at")
        .values_list("product_id", flat=True)
    )

    # Rebuilt through storefront() rather than followed off the wishlist rows,
    # for two reasons: the product card needs the price-range and stock
    # annotations to render a price at all, and a product that has since been
    # unpublished must drop out rather than stay buyable from a saved list.
    products = {
        product.pk: product
        for product in annotate_product_stock(
            Product.objects.storefront().with_price_range().filter(pk__in=saved_order)
        )
    }

    return render(
        request,
        "accounts/wishlist.html",
        {
            # Re-sorted into the order they were saved, newest first, which the
            # pk__in lookup does not preserve.
            "products": [products[pk] for pk in saved_order if pk in products],
            "unavailable_count": len(saved_order) - len(products),
        },
    )
