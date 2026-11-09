from django import forms, template
from django.utils.html import format_html

register = template.Library()


@register.filter
def inr(value):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return value
    if v == 0:
        return "Free"
    s = f"{v:,.2f}".rstrip("0").rstrip(".")
    return f"₹{s}"


@register.filter
def widget_type(field):
    w = field.field.widget
    if isinstance(w, forms.CheckboxInput):
        return "checkbox"
    if isinstance(w, (forms.ClearableFileInput, forms.FileInput)):
        return "file"
    return "other"


@register.filter
def get_item(d, key):
    try:
        return d.get(key)
    except AttributeError:
        return None


@register.simple_tag
def status_badge(value, label=None):
    from apps.dashboard.crud import badge

    return badge(value, label)


@register.filter
def minutes(value):
    try:
        m = int(value)
    except (TypeError, ValueError):
        return ""
    if not m:
        return ""
    h, mm = divmod(m, 60)
    return f"{h}h {mm}m" if h else f"{mm} min"


@register.simple_tag(takes_context=True)
def qs_replace(context, **kwargs):
    q = context["request"].GET.copy()
    for k, v in kwargs.items():
        if v in (None, ""):
            q.pop(k, None)
        else:
            q[k] = v
    return q.urlencode()


@register.filter
def initials(user):
    name = (user.get_full_name() or user.email or "?").split()
    return format_html("{}", "".join(p[0] for p in name[:2]).upper())
