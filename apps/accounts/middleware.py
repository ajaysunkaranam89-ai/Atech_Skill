from django.conf import settings
from django.shortcuts import redirect

# Staff without two-factor set up may only reach these (log out, set up 2FA, public pages).
PROTECTED_PREFIXES = ("/dashboard/", "/learn/", "/api/", "/notifications/", "/django-admin/", "/payments/")


class StaffTwoFactorMiddleware:
    """Force administrators, course admins and trainers to set up an authenticator app
    before they can use any logged-in area (when STAFF_2FA_REQUIRED is on)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if (
            settings.STAFF_2FA_REQUIRED
            and user is not None
            and user.is_authenticated
            and user.is_academy_staff
            and not user.totp_enabled
            and request.path.startswith(PROTECTED_PREFIXES)
        ):
            return redirect("accounts:twofa_setup")
        return self.get_response(request)
