"""Phase 1 corporate UI: header/menu, home sections, corporate page, brochure, newsletter, admin content."""
from django.urls import reverse

from apps.core.models import FAQ, Enquiry, NewsletterSubscriber, SiteSettings, Testimonial
from apps.courses.models import CourseCategory

from .base import LMSTestCase


class CorporateUiTests(LMSTestCase):
    def setUp(self):
        super().setUp()
        self.cat = CourseCategory.objects.create(name="Networking", icon="bi-router")
        self.course = self.make_course(title="CCNA 200-301")
        self.course.category = self.cat
        self.course.save()

    def test_header_menu_footer(self):
        html = self.client.get("/").content.decode()
        self.assertIn("mega-menu", html)
        self.assertIn("CCNA 200-301", html)  # course listed under its category in the menu
        self.assertIn(reverse("core:corporate"), html)
        self.assertIn('id="newsletter"', html)

    def test_home_sections_only_show_real_content(self):
        html = self.client.get("/").content.decode()
        self.assertNotIn("trust-strip", html)  # no stats entered -> nothing invented
        self.assertNotIn("What our learners say", html)
        s = SiteSettings.load()
        s.stat_students, s.certification_paths = "2,500+", "Cisco CCNA\nAWS Solutions Architect"
        s.save()
        Testimonial.objects.create(name="Priya", company="Infosys", quote="Great labs!", is_featured=True)
        FAQ.objects.create(question="Do you offer weekend batches?", answer="Yes.")
        html = self.client.get("/").content.decode()
        for text in ("2,500+", "AWS Solutions Architect", "Great labs!", "Do you offer weekend batches?", '"FAQPage"'):
            self.assertIn(text, html)

    def test_corporate_proposal_lead(self):
        self.assertContains(self.client.get(reverse("core:corporate")), "Request a training proposal")
        self.client.post(reverse("core:enquiry"), {
            "kind": "corporate", "name": "Anil", "phone": "9876543210", "company_name": "Acme Ltd",
            "designation": "IT Manager", "team_size": "25", "training_mode": "On-site", "next": "/corporate-training/"})
        e = Enquiry.objects.get()
        self.assertEqual((e.kind, e.company_name, e.team_size), ("corporate", "Acme Ltd", "25"))

    def test_brochure_download_creates_lead(self):
        r = self.client.post(reverse("core:enquiry"), {"kind": "brochure", "course": self.course.pk, "name": "Ravi",
                                                       "phone": "9876543210", "next": self.course.get_absolute_url()})
        self.assertEqual(r["Content-Type"], "application/pdf")
        self.assertTrue(r.content.startswith(b"%PDF"))
        self.assertEqual(Enquiry.objects.get().kind, "brochure")
        self.assertContains(self.client.get(self.course.get_absolute_url()), "Download the course brochure")

    def test_newsletter_subscribe_and_unsubscribe(self):
        self.client.post(reverse("core:newsletter"), {"email": "Fan@Example.com", "next": "/"})
        self.client.post(reverse("core:newsletter"), {"email": "fan@example.com", "next": "/"})  # no duplicate
        sub = NewsletterSubscriber.objects.get()
        self.assertEqual(sub.email, "fan@example.com")
        self.client.post(reverse("core:newsletter"), {"email": "bot@example.com", "website": "x", "next": "/"})
        self.assertEqual(NewsletterSubscriber.objects.count(), 1)
        self.client.post(reverse("core:unsubscribe", args=[sub.unsubscribe_token]))
        sub.refresh_from_db()
        self.assertFalse(sub.is_active)

    def test_course_faq_and_testimonial(self):
        FAQ.objects.create(question="Is the exam fee included?", answer="No.", course=self.course)
        Testimonial.objects.create(name="Kiran", quote="Cleared CCNA first attempt", course=self.course)
        html = self.client.get(self.course.get_absolute_url()).content.decode()
        self.assertIn("Is the exam fee included?", html)
        self.assertIn("Cleared CCNA first attempt", html)

    def test_admin_content_screens(self):
        self.login(self.course_admin)
        self.client.post(reverse("dashboard:testimonial_add"), {"name": "Asha", "quote": "Loved it", "rating": 5,
                                                                "is_published": "on", "is_featured": "on", "order": 0})
        self.client.post(reverse("dashboard:faq_add"), {"question": "Q?", "answer": "A.", "is_published": "on", "order": 0})
        self.assertTrue(Testimonial.objects.filter(name="Asha").exists())
        self.assertTrue(FAQ.objects.filter(question="Q?").exists())
        NewsletterSubscriber.objects.create(email="a@b.com")
        self.assertIn("a@b.com", self.client.get(reverse("dashboard:subscriber_export")).content.decode())
        self.login(self.trainer)
        self.assertEqual(self.client.get(reverse("dashboard:testimonials")).status_code, 403)
