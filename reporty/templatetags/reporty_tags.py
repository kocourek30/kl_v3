from django import template
from django.utils.timesince import timesince
register = template.Library()

@register.filter
def cz_timeuntil(value):
    delta = timesince(value)
    if 'day' in delta:
        days = int(delta.split())
        if days == 1: return "1 den zbývá"
        elif days in [2,3,4]: return f"{days} dny zbývá"
        else: return f"{days} dnů zbývá"
    return delta + " zbývá"


@register.filter
def dict_get(value, key):
    if isinstance(value, dict):
        return value.get(key, "")
    return ""
