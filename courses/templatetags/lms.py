from django import template

from courses.models import BLOOM_SHORT

register = template.Library()


@register.filter
def get(mapping, key):
    try:
        return mapping.get(key)
    except AttributeError:
        return None


@register.filter
def pct(value, digits=0):
    if value is None or value == "":
        return "–"
    return f"{float(value):.{int(digits)}f}%"


@register.filter
def num(value, digits=1):
    if value is None or value == "":
        return "–"
    value = float(value)
    if value == int(value):
        return str(int(value))
    return f"{value:.{int(digits)}f}"


@register.filter
def bloom(level):
    return f"L{level} {BLOOM_SHORT.get(level, '')}"


@register.filter
def lines(text):
    return [l.strip() for l in (text or "").splitlines() if l.strip()]


@register.filter
def att_class(percent):
    if percent is None:
        return ""
    if percent >= 85:
        return "ok"
    if percent >= 75:
        return "warn"
    return "bad"


@register.filter
def level_class(level):
    return {3: "ok", 2: "info", 1: "warn", 0: "bad"}.get(level, "")


@register.inclusion_tag("includes/field.html")
def field(bound_field):
    return {"f": bound_field}
