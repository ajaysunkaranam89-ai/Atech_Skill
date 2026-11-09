"""Create the first Super Admin from environment variables (idempotent).

Reads DJANGO_SUPERADMIN_EMAIL and DJANGO_SUPERADMIN_PASSWORD. Does nothing if
either is missing or the user already exists. Never creates any course data.
"""
import os

from django.core.management.base import BaseCommand

from apps.accounts.models import Role, User


class Command(BaseCommand):
    help = "Create the initial Super Admin account from environment variables if it does not exist."

    def handle(self, *args, **opts):
        email = (os.environ.get("DJANGO_SUPERADMIN_EMAIL") or "").strip().lower()
        password = os.environ.get("DJANGO_SUPERADMIN_PASSWORD") or ""
        if not email or not password:
            self.stdout.write("DJANGO_SUPERADMIN_EMAIL/PASSWORD not set; skipping.")
            return
        if User.objects.filter(email=email).exists():
            self.stdout.write(f"Super Admin {email} already exists; leaving it unchanged.")
            return
        User.objects.create_superuser(email=email, password=password, first_name="Academy", last_name="Owner", role=Role.SUPER_ADMIN)
        self.stdout.write(self.style.SUCCESS(f"Created Super Admin {email}. Change the password after first login."))
