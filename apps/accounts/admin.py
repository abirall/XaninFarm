"""Django admin for users and addresses."""

from __future__ import annotations

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import AdminPasswordChangeForm
from django.utils.translation import gettext_lazy as _

from apps.accounts.models import Address, User


class AddressInline(admin.TabularInline):
    model = Address
    extra = 0
    fields = ("label", "recipient_name", "phone", "area", "district", "is_default")
    show_change_link = True


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    change_password_form = AdminPasswordChangeForm
    ordering = ("-date_joined",)
    list_display = ("email", "full_name", "phone", "is_active", "is_staff", "date_joined")
    list_filter = ("is_staff", "is_superuser", "is_active", "email_verified", "marketing_opt_in")
    search_fields = ("email", "full_name", "phone")
    readonly_fields = ("date_joined", "last_login")
    inlines = [AddressInline]

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        (_("Personal info"), {"fields": ("full_name", "phone")}),
        (
            _("Permissions"),
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "email_verified",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        (_("Preferences"), {"fields": ("marketing_opt_in",)}),
        (_("Important dates"), {"fields": ("last_login", "date_joined")}),
    )

    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "full_name", "phone", "password1", "password2"),
            },
        ),
    )


@admin.register(Address)
class AddressAdmin(admin.ModelAdmin):
    list_display = (
        "recipient_name",
        "user",
        "phone",
        "area",
        "district",
        "division",
        "delivery_zone",
        "is_default",
    )
    list_filter = ("division", "is_default", "delivery_zone")
    search_fields = ("recipient_name", "phone", "area", "district", "user__email")
    autocomplete_fields = ("user",)
    list_select_related = ("user", "delivery_zone")
