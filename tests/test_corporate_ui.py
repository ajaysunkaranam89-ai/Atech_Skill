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


class CoursePagePhase2Tests(LMSTestCase):
    def test_cards_and_course_page(self):
        from apps.courses.models import Batch

        c = self.make_course(title="AWS Cloud")
        c.is_featured = True
        c.save()
        Batch.objects.create(course=c, name="Weekend", code="AWS-W1", start_date="2026-12-05", capacity=10)
        Batch.objects.create(course=c, name="Closed", code="AWS-X", start_date="2026-11-20", capacity=10, is_open_for_enrollment=False)
        html = self.client.get(reverse("core:course_list")).content.decode()
        self.assertIn("Next batch: <strong>05 Dec 2026</strong>", html)  # closed earlier batch ignored
        self.assertIn("Popular", html)
        page = self.client.get(c.get_absolute_url()).content.decode()
        for text in ('class="course-subnav', 'id="batches"', "Reserve seat", 'id="certificate"', "mobile-cta"):
            self.assertIn(text, page)


class AdminPhase3Tests(LMSTestCase):
    def test_charts_and_quick_create_by_role(self):
        import json

        from apps.enrollments.models import Payment, PaymentGateway, PaymentStatus
        from django.utils import timezone

        c = self.make_course()
        e = self.enroll(self.student, c)
        e.activated_at = timezone.now()
        e.save()
        Payment.objects.create(student=self.student, course=c, gateway=PaymentGateway.UPI, amount=1500,
                               status=PaymentStatus.PAID, paid_at=timezone.now())
        self.login(self.owner)
        r = self.client.get(reverse("dashboard:overview"))
        self.assertContains(r, 'id="chart-data"')
        d = json.loads(json.dumps(r.context["charts"]))
        self.assertEqual(len(d["labels"]), 12)
        self.assertEqual(d["revenue"][-1], 1500.0)
        self.assertEqual(d["enrollments"][-1], 1.0)
        self.assertIn("funnel", d)
        self.assertContains(r, 'class="breadcrumb')
        self.assertContains(r, reverse("dashboard:course_add"))
        self.login(self.trainer)
        d = self.client.get(reverse("dashboard:overview")).context["charts"]
        self.assertNotIn("revenue", d)
        self.assertNotIn("funnel", d)


class PortalPhase4Tests(LMSTestCase):
    def test_continue_card_rings_and_calendar(self):
        from datetime import timedelta

        from django.utils import timezone

        from apps.courses.models import Batch, BatchStudent, LessonProgress, LiveClass

        c = self.make_course()
        m = self.make_module(c)
        l1, l2 = self.make_lesson(m, title="Intro"), self.make_lesson(m, title="Routing")
        self.enroll(self.student, c)
        LessonProgress.objects.create(user=self.student, lesson=l1, completed=True)
        b = Batch.objects.create(course=c, name="Eve", code="E1", start_date="2026-12-01")
        BatchStudent.objects.create(batch=b, student=self.student)
        LiveClass.objects.create(batch=b, title="Live Q&A", meet_url="https://meet.google.com/abc-defg-hij",
                                 start_at=timezone.now() + timedelta(days=1))
        self.login(self.student)
        html = self.client.get(reverse("portal:dashboard")).content.decode()
        self.assertIn("Continue where you left off", html)
        self.assertIn(reverse("portal:lesson", args=[c.slug, l2.pk]), html)  # next incomplete lesson
        self.assertIn("progress-ring", html)
        self.assertIn("calendar.google.com/calendar/render", html)
        self.assertIn("cal-date", html)


class VideoExperienceTests(LMSTestCase):
    def setUp(self):
        super().setUp()
        self.course = self.make_course()
        m = self.make_module(self.course)
        self.l1 = self.make_lesson(m, title="One")
        self.l2 = self.make_lesson(m, title="Two")
        self.enroll(self.student, self.course)
        self.login(self.student)

    def pos(self, lesson, position, duration=600):
        return self.client.post(reverse("api_lesson_position", args=[lesson.pk]),
                                {"position": position, "duration": duration}, content_type="application/json").json()

    def test_resume_and_auto_complete_at_90_percent(self):
        from apps.courses.models import LessonProgress

        self.client.get(self.l1.get_absolute_url())
        for t in range(0, 300, 10):
            d = self.pos(self.l1, t)
        self.assertFalse(d["completed"])
        html = self.client.get(self.l1.get_absolute_url()).content.decode()
        self.assertIn('data-resume="290"', html)
        self.assertIn("Resuming from 4:50", html)
        self.assertIn(self.student.email, html)  # watermark
        for t in range(300, 560, 10):
            d = self.pos(self.l1, t)
        self.assertTrue(d["completed"])
        self.assertEqual(d["progress"]["completed"], 1)
        self.assertGreaterEqual(LessonProgress.objects.get(user=self.student, lesson=self.l1).watched_percent, 90)

    def test_skipping_ahead_does_not_complete(self):
        d = self.pos(self.l2, 590)
        self.assertFalse(d["completed"])
        self.assertEqual(d["watched_percent"], 0)

    def test_outsider_blocked(self):
        self.login(self.outsider)
        r = self.client.post(reverse("api_lesson_position", args=[self.l1.pk]), {"position": 10, "duration": 60},
                             content_type="application/json")
        self.assertEqual(r.status_code, 403)

    def test_why_choose_us_editable(self):
        from apps.core.models import WhyChooseItem

        self.assertIn("Hands-on labs", self.client.get("/").content.decode())  # defaults
        self.login(self.course_admin)
        self.client.post(reverse("dashboard:why_add"), {"icon": "bi-cpu", "title": "Own lab servers",
                                                        "text": "Real racks.", "order": 1, "is_published": "on"})
        self.assertTrue(WhyChooseItem.objects.filter(title="Own lab servers").exists())
        html = self.client.get("/").content.decode()
        self.assertIn("Own lab servers", html)
        self.assertNotIn("Hands-on labs", html)
