"""Manager for the email-based custom user model."""

from __future__ import annotations

from django.contrib.auth.base_user import BaseUserManager
from django.utils.translation import gettext_lazy as _


class UserManager(BaseUserManager):
    """Create users keyed on email instead of a username."""

    use_in_migrations = True

    def _create_user(self, email: str, password: str | None, **extra_fields):
        if not email:
            raise ValueError(_("An email address is required."))

        email = self.normalize_email(email).lower()

        # Empty phone must become NULL: a unique column cannot hold many "".
        phone = extra_fields.get("phone")
        if not phone:
            extra_fields["phone"] = None
        else:
            from apps.core.validators import normalize_bd_phone

            extra_fields["phone"] = normalize_bd_phone(phone)

        user = self.model(email=email, **extra_fields)
        # set_password(None) yields an unusable password, which is what we want
        # for accounts that must go through the reset flow before first login.
        user.set_password(password)
        user.full_clean(exclude=["password"])
        user.save(using=self._db)
        return user

    def create_user(self, email: str, password: str | None = None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email: str, password: str | None = None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)
        extra_fields.setdefault("email_verified", True)

        if extra_fields.get("is_staff") is not True:
            raise ValueError(_("Superuser must have is_staff=True."))
        if extra_fields.get("is_superuser") is not True:
            raise ValueError(_("Superuser must have is_superuser=True."))
        if not extra_fields.get("full_name"):
            extra_fields["full_name"] = email.split("@")[0].title()

        return self._create_user(email, password, **extra_fields)

    def get_by_natural_key(self, username: str | None):
        return self.get(email__iexact=username)
