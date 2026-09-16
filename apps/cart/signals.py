"""Merge the guest cart into the customer's cart at login."""

from __future__ import annotations

import logging

from django.contrib.auth.signals import user_logged_in
from django.db import DatabaseError
from django.dispatch import receiver

from apps.cart.models import Cart
from apps.cart.services import SESSION_CART_KEY, merge_carts

logger = logging.getLogger(__name__)


@receiver(user_logged_in, dispatch_uid="cart.merge_guest_cart")
def merge_guest_cart(sender, request, user, **kwargs) -> None:
    """Fold the anonymous basket into the customer's on sign-in.

    Login has already cycled the session key by the time this fires, so the
    guest cart is found by the id recorded in the (preserved) session data.
    """
    if request is None or not hasattr(request, "session"):
        return

    cart_id = request.session.pop(SESSION_CART_KEY, None)
    if not cart_id:
        return

    guest_cart = Cart.objects.filter(pk=cart_id, user__isnull=True).first()
    if guest_cart is None:
        return

    try:
        merge_carts(session_cart=guest_cart, user=user)
    except DatabaseError:  # pragma: no cover - never block a login over a cart
        logger.exception("Failed to merge guest cart %s for user %s", cart_id, user.pk)
