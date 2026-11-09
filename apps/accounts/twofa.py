"""Two-factor login screens: verify at login, set up, regenerate recovery codes, reset."""
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_POST

from apps.core import audit
from apps.core.audit import client_ip
from apps.core.models import SiteSettings

from . import totp

PENDING_SECONDS = 300
MAX_CODE_ATTEMPTS = 5


def _pending_user(request):
    data = request.session.get("2fa_pending")
    if not data or timezone.now().timestamp() - data.get("ts", 0) > PENDING_SECONDS:
        request.session.pop("2fa_pending", None)
        return None, None
    user = get_user_model().objects.filter(pk=data["uid"], is_active=True).first()
    return user, data


@sensitive_post_parameters("code")
def verify(request):
    """Second login step: 6-digit code from the authenticator app, or a recovery code."""
    user, data = _pending_user(request)
    if user is None:
        messages.info(request, "Please log in again.")
        return redirect("accounts:login")
    error = None
    if request.method == "POST":
        key = f"2fa-fail:{user.pk}:{client_ip(request)}"
        fails = cache.get(key, 0)
        code = request.POST.get("code", "").strip()
        ok = False
        if fails >= MAX_CODE_ATTEMPTS:
            request.session.pop("2fa_pending", None)
            audit.log(request, "2fa_locked", user)
            messages.error(request, "Too many wrong codes. Please log in again in a few minutes.")
            return redirect("accounts:login")
        counter = totp.verify(user.totp_secret, code, user.totp_last_counter)
        if counter is not None:
            user.totp_last_counter = counter
            user.save(update_fields=["totp_last_counter"])
            ok = True
        elif "-" in code and totp.use_recovery_code(user, code):
            ok = True
            audit.log(request, "2fa_recovery_code_used", user)
            messages.warning(request, f"Recovery code used. {len(user.totp_recovery_codes)} left - generate new ones soon.")
        if ok:
            cache.delete(key)
            nxt = data.get("next") or ""
            request.session.pop("2fa_pending", None)
            login(request, user, backend=data.get("backend"))  # rotates the session key
            audit.log(request, "login", user, {"2fa": True})
            if not url_has_allowed_host_and_scheme(nxt, {request.get_host()}, request.is_secure()):
                nxt = ""
            return redirect(nxt or "accounts:after_login")
        cache.set(key, fails + 1, 900)
        audit.log(request, "2fa_failed", user)
        error = "That code is not correct. Check the time on your phone and try again."
    return render(request, "accounts/twofa_verify.html", {"error": error, "email": user.email})


@login_required
@sensitive_post_parameters("code")
def setup(request):
    user = request.user
    if user.totp_enabled:
        return render(request, "accounts/twofa_status.html", {"required": _required(user)})
    secret = request.session.get("2fa_setup_secret") or totp.new_secret()
    request.session["2fa_setup_secret"] = secret
    error = None
    if request.method == "POST":
        counter = totp.verify(secret, request.POST.get("code", ""))
        if counter is not None:
            codes, hashes = totp.new_recovery_codes()
            user.totp_secret = secret
            user.totp_enabled = True
            user.totp_last_counter = counter
            user.totp_recovery_codes = hashes
            user.totp_enabled_at = timezone.now()
            user.save(update_fields=["totp_secret", "totp_enabled", "totp_last_counter", "totp_recovery_codes", "totp_enabled_at"])
            request.session.pop("2fa_setup_secret", None)
            audit.log(request, "2fa_enabled", user)
            return render(request, "accounts/twofa_codes.html", {"codes": codes, "first_time": True})
        error = "That code didn't match. Make sure you scanned the QR code above and try the newest code."
    site = SiteSettings.load()
    uri = totp.provisioning_uri(secret, user.email, site.site_name)
    return render(request, "accounts/twofa_setup.html", {
        "secret": " ".join(secret[i:i + 4] for i in range(0, len(secret), 4)),
        "qr": totp.qr_svg(uri),
        "error": error,
        "required": _required(user),
    })


@login_required
@require_POST
@sensitive_post_parameters("code")
def regenerate_codes(request):
    user = request.user
    if not user.totp_enabled or totp.verify(user.totp_secret, request.POST.get("code", ""), user.totp_last_counter) is None:
        messages.error(request, "Enter a current code from your authenticator app to continue.")
        return redirect("accounts:twofa_setup")
    codes, hashes = totp.new_recovery_codes()
    user.totp_recovery_codes = hashes
    user.save(update_fields=["totp_recovery_codes"])
    audit.log(request, "2fa_codes_regenerated", user)
    return render(request, "accounts/twofa_codes.html", {"codes": codes, "first_time": False})


@login_required
@require_POST
@sensitive_post_parameters("code")
def reset_own(request):
    """Move to a new phone: requires a current code, then set up again."""
    user = request.user
    counter = totp.verify(user.totp_secret, request.POST.get("code", ""), user.totp_last_counter)
    if not user.totp_enabled or counter is None:
        messages.error(request, "Enter a current code from your authenticator app to continue.")
        return redirect("accounts:twofa_setup")
    user.totp_enabled = False
    user.totp_secret = ""
    user.totp_recovery_codes = []
    user.save(update_fields=["totp_enabled", "totp_secret", "totp_recovery_codes"])
    audit.log(request, "2fa_reset", user)
    messages.info(request, "Authenticator removed. Scan the new QR code with your new phone.")
    return redirect("accounts:twofa_setup")


def _required(user):
    return settings.STAFF_2FA_REQUIRED and user.is_academy_staff
