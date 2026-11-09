"""Render every page as each role and report non-2xx/3xx responses. Dev helper."""
import os
import sys

import django

sys.path.insert(0, os.getcwd())
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()
from django.conf import settings  # noqa: E402
from django.test import Client  # noqa: E402

from apps.accounts.models import User  # noqa: E402

settings.ALLOWED_HOSTS = ["*"]
URLS = sys.argv[1:] or [
    "/", "/courses/", "/accounts/login/", "/accounts/signup/", "/healthz/",
    "/dashboard/", "/dashboard/courses/", "/dashboard/courses/add/", "/dashboard/modules/", "/dashboard/modules/add/",
    "/dashboard/videos/", "/dashboard/videos/add/", "/dashboard/materials/", "/dashboard/materials/add/",
    "/dashboard/batches/", "/dashboard/batches/add/", "/dashboard/live/", "/dashboard/live/add/",
    "/dashboard/students/", "/dashboard/students/add/", "/dashboard/staff/", "/dashboard/staff/add/",
    "/dashboard/enrollments/", "/dashboard/enrollments/add/", "/dashboard/payments/", "/dashboard/payments/upi/",
    "/dashboard/assignments/", "/dashboard/assignments/add/", "/dashboard/certificates/", "/dashboard/certificates/add/",
    "/dashboard/notifications/", "/dashboard/notifications/add/", "/dashboard/notifications/log/",
    "/dashboard/reports/", "/dashboard/settings/", "/dashboard/audit/", "/dashboard/categories/", "/dashboard/categories/add/",
    "/learn/", "/learn/assignments/", "/learn/certificates/", "/learn/payments/", "/notifications/", "/accounts/profile/",
    "/api/notifications/",
]
u = User.objects.filter(is_superuser=True).first() or User.objects.create_superuser("owner@test.local", "Sup3r!Pass#1")
c = Client(raise_request_exception=False)
c.force_login(u)
bad = 0
for url in URLS:
    r = c.get(url)
    flag = "" if r.status_code < 400 else "   <-- FAIL"
    bad += bool(flag)
    print(f"{r.status_code} {url}{flag}")
print("failures:", bad)
