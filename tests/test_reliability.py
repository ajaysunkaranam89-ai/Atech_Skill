"""Security & reliability: two-factor login, email queue/worker, backup status, error reporting, framing."""
import json
import tempfile
import time
from datetime import timedelta
from io import StringIO
from pathlib import Path
from unittest import mock

from django.core import mail
from django.core.management import call_command
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts import totp
from apps.core import mail as qmail
from apps.core.models import AuditLog, OutboundEmail

from .base import LMSTestCase


def code_now(secret, offset=0):
    return totp.code_at(secret, totp.current_counter() + offset)


@override_settings(STAFF_2FA_REQUIRED=True)
class TwoFactorTests(LMSTestCase):
    def enable(self, user):
        user.totp_secret = totp.new_secret()
        user.totp_enabled = True
        codes, hashes = totp.new_recovery_codes()
        user.totp_recovery_codes = hashes
        user.save()
        return codes

    def password_login(self, user):
        return self.client.post(reverse("accounts:login"), {"username": user.email, "password": self.password})

    def test_totp_basics(self):
        s = totp.new_secret()
        c = totp.current_counter()
        self.assertEqual(totp.verify(s, totp.code_at(s, c)), c)
        self.assertEqual(totp.verify(s, totp.code_at(s, c - 1)), c - 1)  # clock drift tolerated
        self.assertIsNone(totp.verify(s, totp.code_at(s, c - 3)))  # too old
        self.assertIsNone(totp.verify(s, totp.code_at(s, c), last_counter=c))  # replay rejected
        self.assertIsNone(totp.verify(s, "abc"))
        self.assertIn("otpauth://totp/", totp.provisioning_uri(s, "a@b.c", "A TechSkills Academy"))

    def test_staff_without_2fa_is_forced_to_set_it_up(self):
        self.login(self.course_admin)
        for url in (reverse("dashboard:overview"), reverse("dashboard:courses"), reverse("portal:dashboard")):
            r = self.client.get(url)
            self.assertRedirects(r, reverse("accounts:twofa_setup"), fetch_redirect_response=False)
        # students and public pages are unaffected
        self.login(self.student)
        self.assertEqual(self.client.get(reverse("portal:dashboard")).status_code, 200)

    def test_setup_flow_enables_2fa_and_shows_recovery_codes(self):
        self.login(self.trainer)
        r = self.client.get(reverse("accounts:twofa_setup"))
        self.assertContains(r, "<svg")
        secret = self.client.session["2fa_setup_secret"]
        self.assertContains(self.client.post(reverse("accounts:twofa_setup"), {"code": "000000"}), "Make sure you scanned")
        r = self.client.post(reverse("accounts:twofa_setup"), {"code": code_now(secret)})
        self.assertContains(r, "Save these recovery codes")
        self.trainer.refresh_from_db()
        self.assertTrue(self.trainer.totp_enabled)
        self.assertEqual(len(self.trainer.totp_recovery_codes), 8)
        self.assertEqual(self.client.get(reverse("dashboard:overview")).status_code, 200)
        self.assertTrue(AuditLog.objects.filter(action="2fa_enabled", object_id=str(self.trainer.pk)).exists())

    def test_login_requires_code_when_enabled(self):
        self.enable(self.owner)
        r = self.password_login(self.owner)
        self.assertRedirects(r, reverse("accounts:twofa_verify"), fetch_redirect_response=False)
        # not logged in yet
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(self.client.get(reverse("dashboard:overview")).status_code, 302)
        self.assertContains(self.client.post(reverse("accounts:twofa_verify"), {"code": "123456"}), "not correct")
        self.owner.refresh_from_db()
        r = self.client.post(reverse("accounts:twofa_verify"), {"code": code_now(self.owner.totp_secret)})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.owner.pk)
        self.assertEqual(self.client.get(reverse("dashboard:overview")).status_code, 200)

    def test_code_cannot_be_reused(self):
        self.enable(self.owner)
        self.password_login(self.owner)
        self.owner.refresh_from_db()
        code = code_now(self.owner.totp_secret)
        self.client.post(reverse("accounts:twofa_verify"), {"code": code})
        self.client.logout()
        self.password_login(self.owner)
        self.assertContains(self.client.post(reverse("accounts:twofa_verify"), {"code": code}), "not correct")

    def test_recovery_code_works_once(self):
        codes = self.enable(self.owner)
        self.password_login(self.owner)
        self.assertEqual(self.client.post(reverse("accounts:twofa_verify"), {"code": codes[0]}).status_code, 302)
        self.owner.refresh_from_db()
        self.assertEqual(len(self.owner.totp_recovery_codes), 7)
        self.client.logout()
        self.password_login(self.owner)
        self.assertContains(self.client.post(reverse("accounts:twofa_verify"), {"code": codes[0]}), "not correct")

    def test_brute_force_lockout(self):
        self.enable(self.owner)
        self.password_login(self.owner)
        for _ in range(5):
            self.client.post(reverse("accounts:twofa_verify"), {"code": "000000"})
        r = self.client.post(reverse("accounts:twofa_verify"), {"code": "000000"})
        self.assertRedirects(r, reverse("accounts:login"), fetch_redirect_response=False)
        self.assertNotIn("2fa_pending", self.client.session)

    def test_pending_login_expires(self):
        self.enable(self.owner)
        self.password_login(self.owner)
        s = self.client.session
        s["2fa_pending"]["ts"] -= 600
        s.save()
        r = self.client.get(reverse("accounts:twofa_verify"))
        self.assertRedirects(r, reverse("accounts:login"), fetch_redirect_response=False)

    def test_super_admin_resets_lost_phone(self):
        self.enable(self.owner)
        self.enable(self.trainer)
        self.client.force_login(self.owner)
        self.client.post(reverse("dashboard:staff_reset_2fa", args=[self.trainer.pk]))
        self.trainer.refresh_from_db()
        self.assertFalse(self.trainer.totp_enabled)
        self.assertEqual(self.trainer.totp_secret, "")
        # a course admin cannot reset anyone
        self.enable(self.course_admin)
        self.client.force_login(self.course_admin)
        self.assertEqual(self.client.post(reverse("dashboard:staff_reset_2fa", args=[self.owner.pk])).status_code, 403)

    def test_reset_own_requires_current_code(self):
        self.enable(self.owner)
        self.client.force_login(self.owner)
        self.client.post(reverse("accounts:twofa_reset"), {"code": "000000"})
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.totp_enabled)
        self.client.post(reverse("accounts:twofa_reset"), {"code": code_now(self.owner.totp_secret)})
        self.owner.refresh_from_db()
        self.assertFalse(self.owner.totp_enabled)

    def test_django_admin_disabled_by_default(self):
        self.enable(self.owner)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get("/django-admin/").status_code, 404)


class EmailQueueTests(LMSTestCase):
    @override_settings(EMAIL_BACKEND="apps.core.mail.QueuedEmailBackend",
                       EMAIL_DELIVERY_BACKEND="django.core.mail.backends.locmem.EmailBackend", EMAIL_QUEUE=True)
    def test_emails_are_queued_then_delivered_by_worker(self):
        from django.core.mail import send_mail

        send_mail("Hello", "Body", None, ["x@example.com"])
        self.assertEqual(len(mail.outbox), 0)  # nothing sent during the request
        e = OutboundEmail.objects.get()
        self.assertEqual((e.status, e.to), (OutboundEmail.PENDING, ["x@example.com"]))
        out = StringIO()
        call_command("email_worker", "--once", stdout=out)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].subject, "Hello")
        e.refresh_from_db()
        self.assertEqual(e.status, OutboundEmail.SENT)
        self.assertIsNotNone(qmail.worker_last_seen())

    @override_settings(EMAIL_BACKEND="apps.core.mail.QueuedEmailBackend",
                       EMAIL_DELIVERY_BACKEND="django.core.mail.backends.locmem.EmailBackend", EMAIL_QUEUE=True)
    def test_failures_retry_with_backoff_then_fail_and_can_be_retried(self):
        from django.core.mail import send_mail

        send_mail("Retry me", "Body", None, ["y@example.com"])
        e = OutboundEmail.objects.get()
        with mock.patch("django.core.mail.backends.locmem.EmailBackend.send_messages", side_effect=OSError("SMTP down")):
            for _ in range(6):
                OutboundEmail.objects.filter(pk=e.pk, status=OutboundEmail.PENDING).update(next_attempt_at=timezone.now())
                qmail.deliver_pending()
        e.refresh_from_db()
        self.assertEqual(e.status, OutboundEmail.FAILED)
        self.assertEqual(e.attempts, 6)
        self.assertIn("SMTP down", e.last_error)
        # admin sees it and retries
        self.login(self.owner)
        self.assertContains(self.client.get(reverse("dashboard:email_outbox"), {"status": "failed"}), "Retry me")
        self.client.post(reverse("dashboard:email_retry", args=[e.pk]))
        e.refresh_from_db()
        self.assertEqual((e.status, e.attempts), (OutboundEmail.PENDING, 0))
        qmail.deliver_pending()
        e.refresh_from_db()
        self.assertEqual(e.status, OutboundEmail.SENT)

    @override_settings(EMAIL_QUEUE=True)
    def test_dashboard_warns_when_worker_down(self):
        OutboundEmail.objects.create(subject="s", from_email="a@b.c", to=["z@example.com"])
        self.login(self.owner)
        self.assertContains(self.client.get(reverse("dashboard:overview")), "Emails are not being sent")
        qmail.beat()
        self.assertNotContains(self.client.get(reverse("dashboard:overview")), "Emails are not being sent")

    def test_old_sent_emails_cleaned_up(self):
        OutboundEmail.objects.create(subject="old", from_email="a@b.c", to=["z@x.com"], status="sent",
                                     sent_at=timezone.now() - timedelta(days=40))
        self.assertEqual(qmail.cleanup(), 1)

    def test_only_super_admin_sees_outbox(self):
        self.login(self.course_admin)
        self.assertEqual(self.client.get(reverse("dashboard:email_outbox")).status_code, 403)


class BackupStatusTests(LMSTestCase):
    def setUp(self):
        super().setUp()
        self.dir = Path(tempfile.mkdtemp())

    def write(self, ok=True, hours_ago=1, message="ok"):
        finished = (timezone.now() - timedelta(hours=hours_ago)).isoformat()
        (self.dir / "last.json").write_text(json.dumps({"finished": finished, "ok": ok, "path": "x", "size_kb": 2048, "message": message}))

    def overview(self):
        with self.settings(BACKUP_STATUS_FILE=self.dir / "last.json"):
            self.login(self.owner)
            return self.client.get(reverse("dashboard:overview")).content.decode()

    def test_states(self):
        self.assertIn("No automatic backup has run yet", self.overview())
        self.write(ok=True, hours_ago=2)
        self.assertIn("Last backup", self.overview())
        self.write(ok=True, hours_ago=50)
        self.assertIn("Backups need attention", self.overview())
        self.write(ok=False, message="pg_dump failed")
        html = self.overview()
        self.assertIn("pg_dump failed", html)

    def test_unreadable_status_file_warns_instead_of_crashing(self):
        self.write()
        with mock.patch("pathlib.Path.read_text", side_effect=PermissionError("denied")):
            html = self.overview()
        self.assertIn("Backup status file is unreadable", html)

    def test_not_shown_to_course_admin(self):
        with self.settings(BACKUP_STATUS_FILE=self.dir / "last.json"):
            self.login(self.course_admin)
            self.assertNotIn("backup", self.client.get(reverse("dashboard:overview")).content.decode().lower())


class HardeningTests(LMSTestCase):
    def test_frame_denied(self):
        self.assertEqual(self.client.get("/").headers["X-Frame-Options"], "DENY")

    @override_settings(DEBUG=False, ADMINS=[("Owner", "owner@example.com")])
    def test_server_errors_emailed_to_admins(self):
        import logging

        logging.getLogger("django.request").error("Internal Server Error: /boom", extra={"status_code": 500, "request": None})
        self.assertTrue(any(m.to == ["owner@example.com"] and "TechSkills error" in m.subject for m in mail.outbox))
