"""Login brute-force protection backed by the shared database cache."""
import hashlib

from django.conf import settings
from django.core.cache import cache

from apps.core.audit import client_ip


def _keys(request, email):
    ip = client_ip(request) or "unknown"
    h = hashlib.sha256((email or "").lower().encode()).hexdigest()[:16]
    return f"login:ip:{ip}", f"login:acct:{h}"


def is_locked(request, email):
    ip_key, acct_key = _keys(request, email)
    limit = settings.LOGIN_MAX_ATTEMPTS
    # Per-account limit stops password guessing; a looser per-IP limit stops spraying.
    return (cache.get(acct_key, 0) >= limit) or (cache.get(ip_key, 0) >= limit * 4)


def register_failure(request, email):
    for key in _keys(request, email):
        try:
            cache.incr(key)
        except ValueError:
            cache.set(key, 1, settings.LOGIN_LOCKOUT_SECONDS)


def reset(request, email):
    for key in _keys(request, email):
        cache.delete(key)
