"""Rendering and sending mail.

One helper does the work for every message: render an HTML template, derive
a plain-text alternative from a sibling ``.txt`` template, and send. Keeping
that in one place means the from-address, the reply-to and the absolute-URL
handling are decided once.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import EmailMultiAlternatives
from django.template import TemplateDoesNotExist
from django.template.loader import render_to_string
from django.utils.html import strip_tags

from apps.core.models import SiteSetting

logger = logging.getLogger(__name__)


def absolute_url(path: str) -> str:
    """Turn a site-relative path into something clickable in an inbox."""
    if not path:
        return settings.SITE_URL
    if path.startswith(("http://", "https://")):
        return path
    return f"{settings.SITE_URL}/{path.lstrip('/')}"


def base_context(extra: dict | None = None) -> dict:
    settings_row = SiteSetting.get_solo()
    context = {
        "site": settings_row,
        "site_url": settings.SITE_URL,
        "brand_name": settings_row.brand_name,
        "support_email": settings_row.support_email or settings.ORDER_NOTIFICATION_EMAIL,
        "support_phone": settings_row.support_phone,
        "currency": settings.CURRENCY_SYMBOL,
    }
    context.update(extra or {})
    return context


def send_email(
    *,
    subject: str,
    template: str,
    context: dict,
    to: list[str],
    reply_to: list[str] | None = None,
) -> int:
    """Send one templated message. Returns the number of messages sent.

    Never raises: a mail server being down must not roll back an order that
    has already been paid for.
    """
    recipients = [address for address in to if address]
    if not recipients:
        return 0

    full_context = base_context(context)
    html_body = render_to_string(f"emails/{template}.html", full_context)
    try:
        text_body = render_to_string(f"emails/{template}.txt", full_context)
    except TemplateDoesNotExist:  # no .txt sibling - strip the HTML instead
        text_body = strip_tags(html_body)

    message = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=recipients,
        reply_to=reply_to or [settings.ORDER_NOTIFICATION_EMAIL],
    )
    message.attach_alternative(html_body, "text/html")

    try:
        return message.send(fail_silently=False)
    except Exception:
        logger.exception("Could not send '%s' to %s", subject, recipients)
        return 0


def staff_recipients() -> list[str]:
    """Who gets operational digests."""
    configured = getattr(settings, "OPS_EMAIL_RECIPIENTS", None)
    if configured:
        return list(configured)

    return list(
        get_user_model()
        .objects.filter(is_staff=True, is_active=True)
        .exclude(email="")
        .values_list("email", flat=True)
    )
