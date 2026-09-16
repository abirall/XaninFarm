"""Small shared helpers: money arithmetic, slugs, references."""

from __future__ import annotations

import secrets
import string
from decimal import ROUND_HALF_UP, Decimal

from django.utils.text import slugify

# Money is always stored and compared as a 2-decimal-place Decimal. Never float.
MONEY_QUANTUM = Decimal("0.01")
QUANTITY_QUANTUM = Decimal("0.001")
ZERO = Decimal("0.00")
ZERO_QTY = Decimal("0.000")

# Reusable field kwargs so every money column is identical.
MONEY_FIELD = {"max_digits": 12, "decimal_places": 2}
QUANTITY_FIELD = {"max_digits": 12, "decimal_places": 3}


def money(value: Decimal | int | float | str | None) -> Decimal:
    """Coerce a value to a 2dp Decimal, rounding half-up like a cash register."""
    if value is None:
        return ZERO
    if not isinstance(value, Decimal):
        # str() first: Decimal(float) inherits binary float error.
        value = Decimal(str(value))
    return value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def to_quantity(value: Decimal | int | float | str | None) -> Decimal:
    """Coerce a value to a 3dp Decimal stock quantity."""
    if value is None:
        return ZERO_QTY
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return value.quantize(QUANTITY_QUANTUM, rounding=ROUND_HALF_UP)


def percentage_of(amount: Decimal, percent: Decimal) -> Decimal:
    """Return `percent`% of `amount` as money."""
    return money(money(amount) * money(percent) / Decimal("100"))


def unique_slugify(instance, value: str, field_name: str = "slug", max_length: int = 220) -> str:
    """Build a slug that is unique for the model, appending -2, -3 ... on clash."""
    base = slugify(value)[:max_length] or "item"
    model = instance.__class__
    slug = base
    suffix = 2
    while True:
        queryset = model._default_manager.filter(**{field_name: slug})
        if instance.pk:
            queryset = queryset.exclude(pk=instance.pk)
        if not queryset.exists():
            return slug
        tail = f"-{suffix}"
        slug = f"{base[: max_length - len(tail)]}{tail}"
        suffix += 1


# Excludes easily-confused characters (0/O, 1/I) so codes stay readable aloud.
_UNAMBIGUOUS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def random_code(length: int = 8, alphabet: str = _UNAMBIGUOUS) -> str:
    """Cryptographically-random human-readable code."""
    return "".join(secrets.choice(alphabet) for _ in range(length))


def random_token(length: int = 40) -> str:
    """Random lowercase alphanumeric token for idempotency keys and references."""
    alphabet = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def mask_tail(value: str | None, visible: int = 4) -> str:
    """Mask all but the last `visible` characters - used for logging references."""
    if not value:
        return ""
    text = str(value)
    if len(text) <= visible:
        return "*" * len(text)
    return f"{'*' * (len(text) - visible)}{text[-visible:]}"


def client_ip(request) -> str | None:
    """Best-effort client IP, honouring X-Forwarded-For behind Nginx."""
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip() or None
    return request.META.get("REMOTE_ADDR") or None
