"""View mixins for access control and HTMX-aware responses."""

from __future__ import annotations

import json

from django.contrib.auth.mixins import AccessMixin, LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse


class StaffRequiredMixin(LoginRequiredMixin):
    """Restrict a view to authenticated staff users.

    Anonymous users are redirected to the login page; signed-in non-staff get a
    403 rather than a redirect loop.
    """

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not request.user.is_staff:
            raise PermissionDenied("Staff access required.")
        return super().dispatch(request, *args, **kwargs)


class PermissionRequiredMixin(StaffRequiredMixin, AccessMixin):
    """Staff access plus a specific Django permission."""

    required_permission: str | None = None

    def dispatch(self, request, *args, **kwargs):
        if (
            self.required_permission
            and request.user.is_authenticated
            and not request.user.has_perm(self.required_permission)
        ):
            raise PermissionDenied(f"Missing permission: {self.required_permission}")
        return super().dispatch(request, *args, **kwargs)


class OwnedByUserMixin(LoginRequiredMixin):
    """Scope a queryset to the requesting user.

    Every customer-facing detail view uses this so an object belonging to
    someone else is a 404, never a leak.
    """

    owner_field = "user"

    def get_queryset(self):
        queryset = super().get_queryset()
        return queryset.filter(**{self.owner_field: self.request.user})


class HtmxResponseMixin:
    """Render a partial template when the request came from HTMX."""

    htmx_template_name: str | None = None

    def get_template_names(self):
        if self.htmx_template_name and getattr(self.request, "htmx", False):
            return [self.htmx_template_name]
        return super().get_template_names()


def htmx_trigger(response: HttpResponse, *events: str, **payload) -> HttpResponse:
    """Attach HX-Trigger events so other page fragments can refresh themselves."""
    triggers: dict[str, object] = {event: payload.get(event, {}) for event in events}
    response["HX-Trigger"] = json.dumps(triggers)
    return response
