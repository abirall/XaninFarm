"""Cart badge data for every template render."""

from __future__ import annotations

from django.db import DatabaseError
from django.db.models import Count, Sum

from apps.cart.models import CartItem


def cart_context(request) -> dict:
    """Lightweight cart summary for the header.

    One aggregate query, and none at all for visitors who have never added
    anything. Full pricing is deliberately left to the cart and checkout
    views, which call ``quote_cart``.
    """
    empty = {"cart_item_count": 0, "cart_total_quantity": 0}

    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        cart_filter = {"cart__user": user}
    else:
        session = getattr(request, "session", None)
        session_key = session.session_key if session is not None else None
        if not session_key:
            return empty
        cart_filter = {"cart__user__isnull": True, "cart__session_key": session_key}

    # Runs on every render including error pages, so a database that is down
    # must degrade to an empty badge rather than mask the original exception.
    try:
        totals = CartItem.objects.filter(**cart_filter).aggregate(
            lines=Count("id"), units=Sum("quantity")
        )
    except DatabaseError:
        return empty

    return {
        "cart_item_count": totals["lines"] or 0,
        "cart_total_quantity": totals["units"] or 0,
    }
