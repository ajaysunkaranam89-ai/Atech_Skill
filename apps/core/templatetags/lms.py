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


_NUM_RE = __import__("re").compile(r"^(\d+)[.)]\s+(.*)$")


@register.filter
def rich_text(value):
    """Render a tiny, safe subset of Markdown written by admins in long text fields.

    Supported: '## Heading', '### Subheading', '* item' / '- item' bullets,
    '1. item' numbered lists, '**bold**', blank-line separated paragraphs.
    Everything is HTML-escaped first, so no raw HTML/JS can be injected.
    """
    import re

    from django.utils.html import escape
    from django.utils.safestring import mark_safe

    def inline(text):
        text = escape(text)
        return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)

    out, para, list_tag = [], [], None

    def flush_para():
        if para:
            out.append("<p>" + "<br>".join(inline(p) for p in para) + "</p>")
            para.clear()

    def close_list():
        nonlocal list_tag
        if list_tag:
            out.append(f"</{list_tag}>")
            list_tag = None

    for raw in (value or "").splitlines():
        line = raw.strip()
        if not line:
            flush_para()
            close_list()
            continue
        if line.startswith("### "):
            flush_para(); close_list()
            out.append(f'<h4 class="h6 fw-bold text-navy mt-3">{inline(line[4:])}</h4>')
        elif line.startswith("## "):
            flush_para(); close_list()
            out.append(f'<h3 class="h5 section-title mt-4">{inline(line[3:])}</h3>')
        elif line.startswith(("* ", "- ", "• ")):
            flush_para()
            if list_tag != "ul":
                close_list(); out.append('<ul class="check-list ps-0 row">'); list_tag = "ul"
            out.append(f'<li class="col-md-6">{inline(line[2:])}</li>')
        elif _NUM_RE.match(line):
            flush_para()
            if list_tag != "ol":
                close_list(); out.append("<ol>"); list_tag = "ol"
            out.append(f"<li>{inline(_NUM_RE.match(line).group(2))}</li>")
        else:
            close_list()
            para.append(line)
    flush_para()
    close_list()
    return mark_safe("".join(out))


@register.filter
def first_paragraph(value):
    """Plain-text excerpt: first non-heading paragraph of a rich_text field."""
    for block in (value or "").split("\n\n"):
        b = block.strip()
        if b and not b.startswith(("#", "*", "-", "•")) and not _NUM_RE.match(b):
            return b
    return ""


@register.filter(name="sub")
def subtract(value, arg):
    try:
        return max(int(value) - int(arg), 0)
    except (TypeError, ValueError):
        return value


@register.simple_tag
def jsonld(data):
    """Emit schema.org data as <script type="application/ld+json">, escaped like json_script."""
    import json

    from django.core.serializers.json import DjangoJSONEncoder
    from django.utils.html import _json_script_escapes
    from django.utils.safestring import mark_safe

    if not data:
        return ""
    items = data if isinstance(data, (list, tuple)) else [data]
    out = []
    for d in items:
        payload = json.dumps(d, cls=DjangoJSONEncoder, ensure_ascii=False).translate(_json_script_escapes)
        out.append(f'<script type="application/ld+json">{payload}</script>')
    return mark_safe("\n".join(out))
