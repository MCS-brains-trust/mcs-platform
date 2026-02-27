from django import template
from decimal import Decimal

register = template.Library()


@register.filter
def currency(value):
    """Format a decimal value as AUD currency."""
    try:
        value = Decimal(str(value))
        if value < 0:
            return f"({abs(value):,.2f})"
        return f"{value:,.2f}"
    except (TypeError, ValueError):
        return value


@register.filter
def abs_value(value):
    """Return absolute value."""
    try:
        return abs(Decimal(str(value)))
    except (TypeError, ValueError):
        return value


@register.filter
def get_item(dictionary, key):
    """Get an item from a dictionary by key. Used for amber_indicators lookup."""
    if isinstance(dictionary, dict):
        return dictionary.get(key, [])
    return []
