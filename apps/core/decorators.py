"""Access-control decorators for function-based views.

The class-based equivalents live in ``apps.core.mixins``. Both apply the same
rule, so a view moved between the two styles keeps its access behaviour.
"""

from __future__ import annotations

from functools import wraps

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.http import urlencode


def staff_required(view_func):
    """Restrict a view to authenticated staff.

    Anonymous callers are redirected to the login page with ``?next=``; signed-in
    non-staff get a 403. Redirecting the latter would bounce them between login
    and the dashboard forever, since they are already authenticated.
    """

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        user = request.user
        if not user.is_authenticated:
            login_url = reverse(settings.LOGIN_URL)
            query = urlencode({"next": request.get_full_path()})
            return redirect(f"{login_url}?{query}")
        if not user.is_staff:
            raise PermissionDenied("Staff access required.")
        return view_func(request, *args, **kwargs)

    return wrapper


def permission_required(permission: str):
    """Staff access plus a specific Django permission."""

    def decorator(view_func):
        @wraps(view_func)
        @staff_required
        def wrapper(request, *args, **kwargs):
            if not request.user.has_perm(permission):
                raise PermissionDenied(f"Missing permission: {permission}")
            return view_func(request, *args, **kwargs)

        return wrapper

    return decorator
