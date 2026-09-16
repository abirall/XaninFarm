"""Authentication backend allowing login with either email or mobile number."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.db.models import Q

from apps.core.validators import BD_PHONE_RE, normalize_bd_phone

UserModel = get_user_model()


class EmailOrPhoneBackend(ModelBackend):
    """Resolve credentials by email or phone, then delegate to ModelBackend.

    Subclassing ModelBackend (rather than BaseBackend) keeps Django's
    permission machinery - has_perm, has_module_perms, group permissions -
    working unchanged.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        identifier = username or kwargs.get(UserModel.USERNAME_FIELD) or kwargs.get("email")

        if identifier is None or password is None:
            return None

        identifier = str(identifier).strip()
        lookup = Q(email__iexact=identifier)
        if BD_PHONE_RE.match(identifier.replace(" ", "").replace("-", "")):
            lookup |= Q(phone=normalize_bd_phone(identifier))

        try:
            # A duplicate match is impossible: both columns are unique.
            user = UserModel.objects.get(lookup)
        except UserModel.DoesNotExist:
            # Run the default hasher anyway so a missing account and a wrong
            # password take the same amount of time (timing attack defence).
            UserModel().set_password(password)
            return None
        except UserModel.MultipleObjectsReturned:  # pragma: no cover - defensive
            user = UserModel.objects.filter(lookup).order_by("pk").first()
            if user is None:
                return None

        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
