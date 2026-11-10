from django.conf import settings

from .models import SiteSettings


def site(request):
    from .seo import PRIVATE_PATHS

    path = getattr(request, "path", "/")
    data = {
        "academy": SiteSettings.load(),
        "RAZORPAY_KEY_ID": settings.RAZORPAY_KEY_ID,
        "SITE_URL": settings.SITE_URL,
        "canonical_url": settings.SITE_URL + path,
        "noindex": any(path.startswith(p) for p in PRIVATE_PATHS),
    }
    if not data["noindex"]:
        from django.core.cache import cache

        menu = cache.get("menu_categories_v1")
        if menu is None:
            from apps.courses.models import Course, CourseCategory

            menu = []
            for cat in CourseCategory.objects.filter(is_active=True).order_by("order", "name"):
                courses = list(Course.objects.published().filter(category=cat).order_by("title").values("title", "slug")[:6])
                if courses:
                    menu.append({"name": cat.name, "slug": cat.slug, "icon": cat.icon, "courses": courses})
            other = list(Course.objects.published().filter(category__isnull=True).order_by("title").values("title", "slug")[:6])
            if other:
                menu.append({"name": "Other courses", "slug": "", "icon": "", "courses": other})
            cache.set("menu_categories_v1", menu, 120)
        data["menu_categories"] = menu
    if not data["noindex"] and not path.startswith("/enquiry/"):
        from .forms import EnquiryForm

        initial = {}
        if user := getattr(request, "user", None):
            if user.is_authenticated:
                initial = {"name": user.get_full_name(), "email": user.email, "phone": user.phone}
        data["enquiry_form"] = EnquiryForm(initial=initial, auto_id="enq_%s")
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated and path.startswith("/dashboard/") and user.can_manage_all_content:
        from .models import Enquiry, EnquiryStatus

        data["new_leads"] = Enquiry.objects.filter(status=EnquiryStatus.NEW).count()
    if user is not None and user.is_authenticated:
        data["unread_notifications"] = user.notifications.filter(is_read=False).count()
    return data
