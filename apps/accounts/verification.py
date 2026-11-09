"""Email verification for self-registered students."""
from functools import wraps

from django.conf import settings
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.core.cache import cache
from django.core.mail import send_mail
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from apps.core.models import SiteSettings

RESEND_COOLDOWN_SECONDS = 60


class EmailVerificationTokenGenerator(PasswordResetTokenGenerator):
    """Token becomes invalid once the email is verified or the address changes."""

    key_salt = "apps.accounts.EmailVerificationTokenGenerator"

    def _make_hash_value(self, user, timestamp):
        return f"{user.pk}{user.email}{user.email_verified}{timestamp}"


email_token = EmailVerificationTokenGenerator()


def verification_required(user):
    return (
        user.is_authenticated
        and user.is_student
        and not user.email_verified
        and SiteSettings.load().require_email_verification
    )


def verification_url(user):
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    return settings.SITE_URL + reverse("accounts:verify_email", args=[uid, email_token.make_token(user)])


def send_verification_email(user, force=False):
    """Send the link. Returns False if throttled (one email per minute per user)."""
    key = f"verify-email-sent:{user.pk}"
    if not force and cache.get(key):
        return False
    site = SiteSettings.load()
    send_mail(
        subject=f"[{site.site_name}] Confirm your email address",
        message=(
            f"Hello {user.first_name or user.email},\n\n"
            f"Welcome to {site.site_name}! Please confirm your email address to enroll in courses:\n\n"
            f"{verification_url(user)}\n\n"
            "This link is valid for 3 days. If you did not create this account, you can ignore this email.\n\n"
            f"— {site.site_name}"
        ),
        from_email=None,
        recipient_list=[user.email],
        fail_silently=False,
    )
    cache.set(key, 1, RESEND_COOLDOWN_SECONDS)
    return True


def verified_email_required(view):
    """Block enrollment/payment actions until a self-registered student verifies their email."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if verification_required(request.user):
            url = reverse("accounts:verify_pending") + "?next=" + request.path
            if request.headers.get("x-requested-with") == "XMLHttpRequest" or request.content_type == "application/json":
                return JsonResponse({"ok": False, "error": "Please verify your email address before enrolling."}, status=403)
            if request.method == "POST" and "razorpay/order" in request.path:
                return JsonResponse({"ok": False, "error": "Please verify your email address before enrolling."}, status=403)
            return redirect(url)
        return view(request, *args, **kwargs)

    return wrapper
