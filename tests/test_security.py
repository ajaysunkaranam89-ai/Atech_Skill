"""Spec §12: auth, rate limiting, CSRF, XSS escaping, audit, assignments, certificates."""
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Role, User
from apps.core.models import AuditLog
from apps.courses.models import Assignment, AssignmentSubmission, Certificate

from .base import LMSTestCase, pdf


class AuthSecurityTests(LMSTestCase):
    def test_login_rate_limited(self):
        url = reverse("accounts:login")
        for _ in range(5):
            self.client.post(url, {"username": self.student.email, "password": "wrong"})
        r = self.client.post(url, {"username": self.student.email, "password": self.password})
        self.assertEqual(r.status_code, 429)
        self.assertContains(r, "Too many failed login attempts", status_code=429)
        self.assertTrue(AuditLog.objects.filter(action="login_locked").exists())

    def test_login_redirects_by_role(self):
        r = self.client.post(reverse("accounts:login"), {"username": self.owner.email, "password": self.password}, follow=True)
        self.assertEqual(r.redirect_chain[-1][0], reverse("dashboard:overview"))
        self.client.logout()
        r = self.client.post(reverse("accounts:login"), {"username": "STUDENT@academy.test", "password": self.password}, follow=True)
        self.assertEqual(r.redirect_chain[-1][0], reverse("portal:dashboard"))

    def test_signup_creates_student_only(self):
        r = self.client.post(reverse("accounts:signup"), {
            "first_name": "New", "last_name": "User", "email": "new@x.test", "phone": "",
            "password1": "Complex!Pass99", "password2": "Complex!Pass99", "role": "super_admin"})
        self.assertEqual(r.status_code, 302)
        u = User.objects.get(email="new@x.test")
        self.assertEqual(u.role, Role.STUDENT)
        self.assertFalse(u.is_staff)

    def test_open_redirect_blocked(self):
        self.login(self.student)
        r = self.client.get(reverse("accounts:after_login") + "?next=https://evil.example.com/")
        self.assertEqual(r["Location"], reverse("portal:dashboard"))

    def test_csrf_enforced(self):
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.owner)
        r = c.post(reverse("dashboard:course_add"), {"title": "x"})
        self.assertEqual(r.status_code, 403)

    def test_xss_escaped(self):
        c = self.make_course(title="<script>alert(1)</script>")
        r = self.client.get(c.get_absolute_url())
        self.assertNotContains(r, "<script>alert(1)</script>")
        self.assertContains(r, "&lt;script&gt;alert(1)&lt;/script&gt;")

    def test_only_super_admin_manages_admins_and_settings(self):
        for u in (self.course_admin, self.trainer, self.student):
            self.login(u)
            self.assertEqual(self.client.get(reverse("dashboard:staff")).status_code, 403)
            self.assertEqual(self.client.get(reverse("dashboard:settings")).status_code, 403)
            self.assertEqual(self.client.get(reverse("dashboard:payments")).status_code, 403)
        self.login(self.owner)
        r = self.client.post(reverse("dashboard:staff_add"), {
            "first_name": "New", "last_name": "Admin", "email": "admin2@x.test", "role": Role.COURSE_ADMIN,
            "is_active": "on", "password": "Another!Pass77"})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(User.objects.get(email="admin2@x.test").is_course_admin)

    def test_course_admin_cannot_escalate_student_role(self):
        self.login(self.course_admin)
        self.client.post(reverse("dashboard:student_edit", args=[self.student.pk]), {
            "first_name": "Sam", "last_name": "", "email": self.student.email, "role": Role.SUPER_ADMIN, "is_active": "on"})
        self.student.refresh_from_db()
        self.assertEqual(self.student.role, Role.STUDENT)

    def test_site_settings_editable(self):
        self.login(self.owner)
        r = self.client.get(reverse("dashboard:settings"))
        form = r.context["form"]
        data = {k: ("on" if v is True else ("" if v in (None, False) else v)) for k, v in form.initial.items() if k not in ("logo", "upi_qr_code", "id")}
        data.update({"site_name": "A TechSkills Academy", "tagline": "Learn. Practice. Build Your IT Career.", "upi_id": "academy@okicici"})
        r = self.client.post(reverse("dashboard:settings"), data)
        self.assertEqual(r.status_code, 302)
        self.assertContains(self.client.get("/"), "Learn. Practice. Build Your IT Career.")

    def test_healthz(self):
        self.assertEqual(self.client.get("/healthz/").json()["status"], "ok")


class AssignmentCertificateTests(LMSTestCase):
    def setUp(self):
        super().setUp()
        self.course = self.make_course()
        self.enroll(self.student, self.course)

    def test_assignment_flow(self):
        self.login(self.trainer)
        self.client.post(reverse("dashboard:assignment_add"), {
            "course": self.course.pk, "title": "Build a CI pipeline", "instructions": "Use Jenkins", "max_marks": 50,
            "is_published": "on"})
        a = Assignment.objects.get()
        self.login(self.student)
        r = self.client.post(reverse("portal:assignment_detail", args=[a.pk]), {"text_answer": "Done", "file": pdf()})
        self.assertEqual(r.status_code, 302)
        sub = AssignmentSubmission.objects.get()
        self.login(self.outsider)
        self.assertEqual(self.client.get(reverse("portal:submission_file", args=[sub.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("portal:assignment_detail", args=[a.pk])).status_code, 403)
        self.login(self.trainer)
        r = self.client.post(reverse("dashboard:grade_submission", args=[sub.pk]), {"status": "graded", "marks": 60, "feedback": ""})
        self.assertContains(r, "cannot exceed")
        self.client.post(reverse("dashboard:grade_submission", args=[sub.pk]), {"status": "graded", "marks": 45, "feedback": "Good"})
        sub.refresh_from_db()
        self.assertEqual(sub.marks, 45)
        self.login(self.other_trainer)
        self.assertEqual(self.client.get(reverse("dashboard:grade_submission", args=[sub.pk])).status_code, 403)

    def test_certificate_issue_and_verify(self):
        self.login(self.owner)
        self.client.post(reverse("dashboard:certificate_add"), {"student": self.student.pk, "course": self.course.pk, "grade": "A"})
        cert = Certificate.objects.get()
        self.assertTrue(cert.certificate_number.startswith("ATSA-"))
        self.client.logout()
        r = self.client.get(reverse("core:certificate_verify", args=[cert.certificate_number]))
        self.assertContains(r, "Valid certificate")
        self.login(self.outsider)
        self.assertEqual(self.client.get(reverse("portal:certificate", args=[cert.certificate_number])).status_code, 403)
        self.login(self.owner)
        self.client.post(reverse("dashboard:certificate_add"), {"student": self.outsider.pk, "course": self.course.pk})
        self.assertEqual(Certificate.objects.count(), 1)  # not enrolled -> refused


class BrandingTests(LMSTestCase):
    def test_site_name_on_auth_pages(self):
        # Django's auth views inject their own `site` context; branding must still render.
        for url in (reverse("accounts:login"), reverse("accounts:signup"), reverse("accounts:password_reset"), "/"):
            self.assertContains(self.client.get(url), "A TechSkills Academy")
