from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.sitemaps.views import sitemap
from django.urls import include, path
from django.views.decorators.cache import cache_page

from apps.core.seo import SITEMAPS, robots_txt

urlpatterns = [
    path("sitemap.xml", cache_page(60 * 60)(sitemap), {"sitemaps": SITEMAPS}, name="sitemap"),
    path("robots.txt", robots_txt, name="robots"),
    path("", include("apps.core.urls")),
    path("accounts/", include("apps.accounts.urls")),
    path("learn/", include("apps.portal.urls")),
    path("payments/", include("apps.enrollments.urls")),
    path("notifications/", include("apps.notifications.urls")),
    path("dashboard/", include("apps.dashboard.urls")),
    path("api/", include("apps.portal.api_urls")),
]

if settings.DJANGO_ADMIN_ENABLED:
    from django.contrib.auth.views import redirect_to_login

    def _admin_login(request, extra_context=None):
        return redirect_to_login(request.GET.get("next", "/django-admin/"))

    admin.site.login = _admin_login  # never bypass two-factor login
    urlpatterns.append(path("django-admin/", admin.site.urls))

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

handler403 = "apps.core.views.error_403"
handler404 = "apps.core.views.error_404"
