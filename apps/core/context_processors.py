from django.conf import settings

from .models import SiteSettings


def site(request):
    data = {"academy": SiteSettings.load(), "RAZORPAY_KEY_ID": settings.RAZORPAY_KEY_ID}
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        data["unread_notifications"] = user.notifications.filter(is_read=False).count()
    return data
