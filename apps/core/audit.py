from django.db.models import Model
from django.forms.models import model_to_dict

from .models import AuditLog


def client_ip(request):
    if request is None:
        return None
    # Nginx sets X-Real-IP; never trust arbitrary X-Forwarded-For chains blindly.
    ip = request.META.get("HTTP_X_REAL_IP") or request.META.get("REMOTE_ADDR")
    return ip or None


def _jsonable(value):
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if isinstance(value, Model):
        return value.pk
    return str(value)


def diff_form(form):
    """Return {field: [old, new]} for a bound ModelForm that has changed."""
    changes = {}
    for name in form.changed_data:
        if name.startswith("notify") or "password" in name:
            continue
        new = form.cleaned_data.get(name)
        old = form.initial.get(name)
        changes[name] = [_jsonable(old), _jsonable(new)]
    return changes


def log(request, action, obj=None, changes=None, object_type=None, object_repr=None):
    actor = getattr(request, "user", None) if request is not None else None
    if actor is not None and not actor.is_authenticated:
        actor = None
    return AuditLog.objects.create(
        actor=actor,
        action=action,
        object_type=object_type or (obj._meta.model_name if obj is not None else ""),
        object_id=str(obj.pk) if obj is not None and obj.pk else "",
        object_repr=(object_repr or (str(obj) if obj is not None else ""))[:255],
        changes={k: _jsonable(v) for k, v in (changes or {}).items()},
        ip_address=client_ip(request),
    )


def snapshot(obj, fields=None):
    return {k: _jsonable(v) for k, v in model_to_dict(obj, fields=fields).items()}
