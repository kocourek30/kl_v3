from django import template

register = template.Library()


@register.filter
def dict_get(value, key):
    """Bezpečné čtení hodnoty ze slovníku v šabloně."""
    if isinstance(value, dict):
        return value.get(key)
    return None


@register.filter
def dictsum(dictionary, _unused=None):
    """Součet délek všech hodnot ve slovníku."""
    if not isinstance(dictionary, dict):
        return 0
    return sum(len(items) for items in dictionary.values())


@register.filter
def sum_lengths(value):
    """Sečte délky všech seznamů ve slovníku/listu."""
    try:
        return sum(len(v) for v in value)
    except Exception:
        return 0

