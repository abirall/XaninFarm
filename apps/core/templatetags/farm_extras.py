"""Template filters and tags for money, ratings and query-string handling."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django import template
from django.conf import settings
from django.http import QueryDict
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from apps.core.utils import money as to_money

register = template.Library()


@register.filter(name="taka")
def taka(value) -> str:
    """Format a money value as ৳1,250.00 - the storefront's only money filter."""
    try:
        amount = to_money(value)
    except (InvalidOperation, TypeError, ValueError):
        return ""
    return f"{settings.CURRENCY_SYMBOL}{amount:,.2f}"


@register.filter(name="taka_short")
def taka_short(value) -> str:
    """Format money without decimals when the amount is whole (৳1,250)."""
    try:
        amount = to_money(value)
    except (InvalidOperation, TypeError, ValueError):
        return ""
    if amount == amount.to_integral_value():
        return f"{settings.CURRENCY_SYMBOL}{amount:,.0f}"
    return f"{settings.CURRENCY_SYMBOL}{amount:,.2f}"


@register.filter(name="quantity")
def quantity(value) -> str:
    """Trim trailing zeros from a 3dp quantity: 1.500 -> 1.5, 2.000 -> 2."""
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return ""
    normalized = amount.normalize()
    # normalize() can yield exponent form (1E+1); expand it back out.
    if normalized == normalized.to_integral_value():
        return f"{normalized.to_integral_value():f}"
    return f"{normalized:f}"


@register.filter(name="percent_off")
def percent_off(price, compare_at) -> int:
    """Discount percentage, rounded to a whole number. 0 when not on offer."""
    try:
        price_value = to_money(price)
        compare_value = to_money(compare_at)
    except (InvalidOperation, TypeError, ValueError):
        return 0
    if compare_value <= 0 or price_value >= compare_value:
        return 0
    return int(round((compare_value - price_value) / compare_value * 100))


@register.simple_tag(name="star_rating")
def star_rating(value, max_stars: int = 5) -> str:
    """Accessible star rating: filled/half/empty glyphs plus a text label."""
    try:
        rating = float(value or 0)
    except (TypeError, ValueError):
        rating = 0.0
    rating = max(0.0, min(float(max_stars), rating))

    stars = []
    for index in range(1, max_stars + 1):
        if rating >= index:
            state = "full"
        elif rating >= index - 0.5:
            state = "half"
        else:
            state = "empty"
        colour = "text-honey-400" if state != "empty" else "text-charcoal-200"
        glyph = "★" if state == "full" else ("◐" if state == "half" else "☆")
        stars.append(f'<span class="{colour}" aria-hidden="true">{glyph}</span>')

    return format_html(
        '<span class="inline-flex items-center gap-0.5" role="img" aria-label="{} out of {} stars">'
        "{}</span>",
        f"{rating:.1f}",
        max_stars,
        mark_safe("".join(stars)),  # noqa: S308 - glyphs are generated above, not user input
    )


@register.simple_tag(takes_context=True)
def query_replace(context, **kwargs) -> str:
    """Rebuild the current query string with overrides.

    Used by shop filters and pagination so existing filters survive a click:
        <a href="?{% query_replace page=2 %}">
    A value of None or "" removes the parameter entirely.
    """
    request = context.get("request")
    params = request.GET.copy() if request is not None else QueryDict("", mutable=True)

    for key, value in kwargs.items():
        if value in (None, ""):
            params.pop(key, None)
        else:
            params[key] = value
    params.pop("_", None)
    return params.urlencode()


@register.filter(name="attr")
def add_attr(field, attribute: str):
    """Add an HTML attribute to a bound form field: {{ field|attr:"class:input" }}."""
    key, _, value = attribute.partition(":")
    existing = field.field.widget.attrs.copy()
    if key == "class" and existing.get("class"):
        value = f"{existing['class']} {value}"
    existing[key] = value
    return field.as_widget(attrs=existing)


@register.filter(name="dict_get")
def dict_get(mapping, key):
    """Look up a dictionary key whose name is only known at render time."""
    if hasattr(mapping, "get"):
        return mapping.get(key)
    return None
