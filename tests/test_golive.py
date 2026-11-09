"""Go-live hardening: policy pages, batch seat limits, email verification."""
import re
from unittest import mock

from django.core import mail
from django.urls import reverse

from apps.accounts.models import User
from apps.core.models import SiteSettings
from apps.courses.access import valid_enrollment
from apps.courses.models import Batch, BatchStudent
from apps.enrollments.models import Enrollment
from apps.enrollments.services import activate_enrollment
from apps.notifications.models import Notification

from .base import LMSTestCase


class PolicyPageTests(LMSTestCase):
    def test_default_policies_use_contact_details(self):
        s = SiteSettings.load()
        s.contact_email = "help@academy.test"
        s.legal_business_name = "A TechSkills Academy (Prop. Ajay)"
        s.refund_window_days = 10
        s.save()
        for name, text in [("core:privacy", "Privacy Policy"), ("core:terms", "Terms &amp; Conditions"),
                           ("core:refund", "within <strong>10 days</strong>")]:
            r = self.client.get(reverse(name))
            self.assertContains(r, text)
            self.assertContains(r, "help@academy.test")
            self.assertContains(r, "A TechSkills Academy (Prop. Ajay)")
        self.assertContains(self.client.get(reverse("core:contact")), "help@academy.test")
        home = self.client.get("/")
        for name in ("core:privacy", "core:terms", "core:refund", "core:contact"):
            self.assertContains(home, reverse(name))

    def test_custom_policy_overrides_template_and_is_escaped(self):
        s = SiteSettings.load()
        s.refund_policy = "## Our refunds\n\nNo refunds after the first class. <script>x</script>"
        s.save()
        r = self.client.get(reverse("core:refund"))
        self.assertContains(r, "No refunds after the first class.")
        self.assertNotContains(r, "<script>x</script>")
        self.assertNotContains(r, "Processing time")

    def test_signup_requires_terms(self):
        data = {"first_name": "A", "last_name": "B", "email": "t@x.test", "password1": "Complex!Pass99",
                "password2": "Complex!Pass99"}
        r = self.client.post(reverse("accounts:signup"), data)
        self.assertContains(r, "Please accept the Terms")
        self.assertFalse(User.objects.filter(email="t@x.test").exists())


class BatchCapacityTests(LMSTestCase):
    def setUp(self):
        super().setUp()
        self.course = self.make_course(price=0, title="Free Linux")
        self.paid = self.make_course(price=999, title="Paid AWS")
        self.batch = Batch.objects.create(course=self.course, name="Morning", code="LIN-1", start_date="2026-12-01", capacity=1)
        self.pbatch = Batch.objects.create(course=self.paid, name="Evening", code="AWS-1", start_date="2026-12-01", capacity=1)
        BatchStudent.objects.create(batch=self.batch, student=self.outsider)
        BatchStudent.objects.create(batch=self.pbatch, student=self.outsider)

    def test_full_batch_shown_and_rejected(self):
        self.login(self.student)
        r = self.client.get(reverse("enrollments:checkout", args=[self.course.slug]))
        self.assertContains(r, "FULL")
        r = self.client.post(reverse("enrollments:enroll_free", args=[self.course.slug]), {"batch": self.batch.pk}, follow=True)
        self.assertContains(r, "is full")
        self.assertIsNone(valid_enrollment(self.student, self.course))
        self.assertContains(self.client.get(self.course.get_absolute_url()), "Full")

    def test_razorpay_order_refused_for_full_batch(self):
        self.login(self.student)
        with mock.patch("apps.enrollments.services._razorpay_request") as m:
            r = self.client.post(reverse("enrollments:razorpay_order", args=[self.paid.slug]), {"batch": self.pbatch.pk})
        self.assertEqual(r.status_code, 400)
        self.assertIn("full", r.json()["error"])
        m.assert_not_called()

    def test_seat_taken_between_payment_and_activation(self):
        # Paid for a batch that filled up meanwhile: access granted, no batch, admin notified.
        activate_enrollment(self.student, self.paid, "razorpay", batch=self.pbatch)
        e = Enrollment.objects.get(student=self.student, course=self.paid)
        self.assertTrue(e.is_valid)
        self.assertIsNone(e.batch)
        self.assertIn("full", e.notes)
        self.assertEqual(self.pbatch.batch_students.count(), 1)
        self.assertTrue(Notification.objects.filter(user=self.owner, title__startswith="Batch full").exists())

    def test_admin_cannot_overfill_without_force(self):
        self.enroll(self.student, self.course)
        self.login(self.owner)
        url = reverse("dashboard:batch_detail", args=[self.batch.pk])
        r = self.client.post(url, {"student": self.student.pk}, follow=True)
        self.assertContains(r, "is full")
        self.assertEqual(self.batch.batch_students.count(), 1)
        self.client.post(url, {"student": self.student.pk, "force": "1"})
        self.assertEqual(self.batch.batch_students.count(), 2)

    def test_free_seat_is_assigned(self):
        self.batch.capacity = 2
        self.batch.save()
        self.login(self.student)
        self.client.post(reverse("enrollments:enroll_free", args=[self.course.slug]), {"batch": self.batch.pk})
        self.assertEqual(Enrollment.objects.get(student=self.student, course=self.course).batch, self.batch)
        self.assertTrue(self.batch.is_full)


class EmailVerificationTests(LMSTestCase):
    def signup(self, email="newbie@x.test"):
        return self.client.post(reverse("accounts:signup"), {
            "first_name": "New", "last_name": "Bie", "email": email, "phone": "",
            "password1": "Complex!Pass99", "password2": "Complex!Pass99", "accept_terms": "on"})

    def link_path(self):
        url = re.search(r"https?://\S+/accounts/verify-email/\S+/", mail.outbox[-1].body).group(0)
        return "/" + url.split("://", 1)[1].split("/", 1)[1]

    def test_signup_sends_link_and_blocks_enrollment_until_verified(self):
        course = self.make_course(price=0, title="Free Git")
        paid = self.make_course(price=500, title="Paid K8s")
        self.signup()
        user = User.objects.get(email="newbie@x.test")
        self.assertFalse(user.email_verified)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Confirm your email", mail.outbox[0].subject)
        r = self.client.get(reverse("enrollments:checkout", args=[course.slug]))
        self.assertTrue(r["Location"].startswith(reverse("accounts:verify_pending")))
        self.client.post(reverse("enrollments:enroll_free", args=[course.slug]))
        self.assertIsNone(valid_enrollment(user, course))
        r = self.client.post(reverse("enrollments:razorpay_order", args=[paid.slug]))
        self.assertEqual(r.status_code, 403)
        self.assertContains(self.client.get(reverse("portal:dashboard")), "Please confirm your email")

        path = self.link_path()
        r = self.client.get(path, follow=True)
        self.assertContains(r, "Your email is verified")
        user.refresh_from_db()
        self.assertTrue(user.email_verified)
        self.client.post(reverse("enrollments:enroll_free", args=[course.slug]))
        self.assertIsNotNone(valid_enrollment(user, course))
        self.assertContains(self.client.get(path, follow=True), "already verified")

    def test_bad_token_and_resend_throttle(self):
        self.signup()
        user = User.objects.get(email="newbie@x.test")
        from django.utils.encoding import force_bytes
        from django.utils.http import urlsafe_base64_encode

        r = self.client.get(f"/accounts/verify-email/{urlsafe_base64_encode(force_bytes(user.pk))}/bad-token/", follow=True)
        self.assertContains(r, "invalid or has expired")
        r = self.client.post(reverse("accounts:resend_verification"), follow=True)
        self.assertContains(r, "less than a minute ago")
        self.assertEqual(len(mail.outbox), 1)

    def test_toggle_off_and_admin_created_users_not_gated(self):
        course = self.make_course(price=0, title="Free Py")
        self.signup()
        s = SiteSettings.load()
        s.require_email_verification = False
        s.save()
        self.client.post(reverse("enrollments:enroll_free", args=[course.slug]))
        self.assertIsNotNone(valid_enrollment(User.objects.get(email="newbie@x.test"), course))
        self.assertTrue(self.student.email_verified)

    def test_verify_pending_blocks_open_redirect(self):
        self.login(self.student)
        r = self.client.get(reverse("accounts:verify_pending") + "?next=//evil.example.com/")
        self.assertEqual(r["Location"], reverse("portal:dashboard"))
