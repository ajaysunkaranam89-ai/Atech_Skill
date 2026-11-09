"""Lead capture (enquiries, attribution, admin inbox) and SEO/analytics settings."""
from django.core import mail
from django.urls import reverse

from apps.core.models import Enquiry, EnquiryStatus, SiteSettings
from apps.notifications.models import Notification

from .base import LMSTestCase


class EnquiryTests(LMSTestCase):
    def setUp(self):
        super().setUp()
        self.course = self.make_course(title="CCNA Bootcamp")

    def submit(self, **over):
        data = {"name": "Ravi Kumar", "phone": "98765 43210", "email": "ravi@example.com", "course": self.course.pk,
                "kind": "demo", "preferred_time": "Evenings", "message": "Want weekend batch", "website": "",
                "next": self.course.get_absolute_url()}
        data.update(over)
        return self.client.post(reverse("core:enquiry"), data)

    def test_course_page_shows_lead_form_with_course_preselected(self):
        html = self.client.get(self.course.get_absolute_url()).content.decode()
        self.assertIn("Talk to a course advisor", html)
        self.assertIn(f'<input type="hidden" name="course" value="{self.course.pk}">', html)
        self.assertIn("Book a free demo", self.client.get("/").content.decode())
        self.assertIn("Send us an enquiry", self.client.get(reverse("core:contact")).content.decode())

    def test_submit_with_attribution_and_admin_alert(self):
        self.client.get("/?utm_source=google&utm_medium=cpc&utm_campaign=ccna-nov", HTTP_REFERER="https://www.google.com/")
        r = self.submit()
        self.assertRedirects(r, self.course.get_absolute_url() + "#enquiry-thanks", fetch_redirect_response=False)
        e = Enquiry.objects.get()
        self.assertEqual(e.phone, "+91 9876543210")
        self.assertEqual(e.whatsapp_phone, "919876543210")
        self.assertEqual((e.utm_source, e.utm_medium, e.utm_campaign), ("google", "cpc", "ccna-nov"))
        self.assertEqual(e.source_label, "google / cpc / ccna-nov")
        self.assertEqual(e.status, EnquiryStatus.NEW)
        # super admin + course admin alerted (dashboard + email); students/trainers not
        alerted = set(Notification.objects.filter(title__startswith="New lead").values_list("user__email", flat=True))
        self.assertEqual(alerted, {self.owner.email, self.course_admin.email})
        self.assertTrue(any("New lead: Ravi Kumar" in m.subject for m in mail.outbox))

    def test_referrer_attribution_without_utm(self):
        self.client.get("/courses/", HTTP_REFERER="https://www.bing.com/search?q=ccna")
        self.submit()
        self.assertEqual(Enquiry.objects.get().source_label, "bing.com")

    def test_spam_and_validation(self):
        self.submit(website="http://spam.example")  # honeypot filled
        self.submit(phone="12")
        self.assertFalse(Enquiry.objects.exists())

    def test_rate_limit(self):
        for i in range(5):
            self.submit(phone=f"98765432{10 + i}")
        r = self.submit(phone="9876543299")
        self.assertEqual(r.status_code, 429)
        self.assertEqual(Enquiry.objects.count(), 5)

    def test_open_redirect_blocked(self):
        r = self.submit(next="https://evil.example.com/")
        self.assertEqual(r["Location"], "/#enquiry-thanks")


class EnquiryAdminTests(LMSTestCase):
    def setUp(self):
        super().setUp()
        self.enq = Enquiry.objects.create(name="Priya", phone="+91 9000000001", utm_source="instagram")

    def test_access(self):
        for u, code in [(self.student, 403), (self.trainer, 403), (self.course_admin, 200), (self.owner, 200)]:
            self.login(u)
            self.assertEqual(self.client.get(reverse("dashboard:enquiries")).status_code, code, u.email)

    def test_update_export_and_kpis(self):
        self.login(self.course_admin)
        r = self.client.get(reverse("dashboard:enquiries"), {"utm_source": "instagram"})
        self.assertContains(r, "Priya")
        self.assertContains(self.client.get(reverse("dashboard:overview")), "New leads")
        self.client.post(reverse("dashboard:enquiry_detail", args=[self.enq.pk]), {
            "status": "contacted", "assigned_to": self.course_admin.pk, "follow_up_on": "2026-11-20", "notes": "Call back Friday"})
        self.enq.refresh_from_db()
        self.assertEqual(self.enq.status, "contacted")
        self.assertEqual(self.enq.assigned_to, self.course_admin)
        csv = self.client.get(reverse("dashboard:enquiry_export")).content.decode()
        self.assertIn("Priya", csv)
        self.assertIn("Call back Friday", csv)


class SeoSettingsTests(LMSTestCase):
    def settings_post(self, **over):
        self.login(self.owner)
        form = self.client.get(reverse("dashboard:settings")).context["form"]
        data = {k: ("on" if v is True else ("" if v in (None, False) else v)) for k, v in form.initial.items()
                if k not in ("logo", "upi_qr_code", "id")}
        data.update(over)
        return self.client.post(reverse("dashboard:settings"), data)

    def test_analytics_verification_and_home_title(self):
        r = self.settings_post(google_analytics_id="g-abc123xyz",
                               google_site_verification='<meta name="google-site-verification" content="AbCdEf123456_-xyz" />',
                               seo_home_title="IT Training Institute in Bangalore | CCNA AWS DevOps", city="Bangalore")
        self.assertEqual(r.status_code, 302)
        s = SiteSettings.load()
        self.assertEqual(s.google_analytics_id, "G-ABC123XYZ")
        self.assertEqual(s.google_site_verification, "AbCdEf123456_-xyz")
        self.client.logout()
        home = self.client.get("/").content.decode()
        self.assertIn("<title>IT Training Institute in Bangalore | CCNA AWS DevOps</title>", home)
        self.assertIn('content="AbCdEf123456_-xyz"', home)
        self.assertIn("gtag/js?id=G-ABC123XYZ", home)
        self.login(self.owner)
        self.assertNotIn("gtag/js", self.client.get(reverse("dashboard:overview")).content.decode())
        self.assertContains(self.settings_post(google_analytics_id="UA-1234"), "Google Analytics 4 measurement ID")

    def test_course_seo_fields(self):
        c = self.make_course(title="Kubernetes")
        c.seo_title = "Kubernetes CKA Training Online | Live Classes"
        c.seo_description = "Hands-on CKA prep with live labs."
        c.save()
        html = self.client.get(c.get_absolute_url()).content.decode()
        self.assertIn("<title>Kubernetes CKA Training Online | Live Classes</title>", html)
        self.assertIn('<meta name="description" content="Hands-on CKA prep with live labs.">', html)
