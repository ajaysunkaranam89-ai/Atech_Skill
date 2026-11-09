"""Item 12: sitemap, robots.txt, structured data, meta tags, About and Trainers pages."""
import json
import re

from django.test import override_settings
from django.urls import reverse

from apps.courses.models import CourseStatus

from .base import LMSTestCase


def ld_blocks(html):
    return [json.loads(m) for m in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)]


@override_settings(SITE_URL="https://academy.example.com")
class SeoTests(LMSTestCase):
    def setUp(self):
        super().setUp()
        self.pub = self.make_course(title="AWS Cloud Practitioner", price=4999)
        self.draft = self.make_course(title="Secret Draft", status=CourseStatus.DRAFT)
        self.trainer_profile.bio = "## About\n\nTara teaches cloud."
        self.trainer_profile.save()

    def test_sitemap_lists_public_pages_only(self):
        r = self.client.get("/sitemap.xml")
        self.assertEqual(r.status_code, 200)
        body = r.content.decode()
        self.assertIn(f"<loc>https://academy.example.com{self.pub.get_absolute_url()}</loc>", body)
        self.assertNotIn(self.draft.slug, body)
        self.assertIn(f"https://academy.example.com/trainers/{self.trainer_profile.pk}/", body)
        for path in ("/about/", "/trainers/", "/contact/", "/refund-policy/"):
            self.assertIn(f"https://academy.example.com{path}", body)
        self.assertNotIn("/dashboard/", body)

    def test_robots(self):
        body = self.client.get("/robots.txt").content.decode()
        for p in ("/dashboard/", "/learn/", "/accounts/", "/payments/", "/api/"):
            self.assertIn(f"Disallow: {p}", body)
        self.assertIn("Sitemap: https://academy.example.com/sitemap.xml", body)

    def test_course_structured_data_and_meta(self):
        html = self.client.get(self.pub.get_absolute_url()).content.decode()
        blocks = ld_blocks(html)
        course = next(b for b in blocks if b["@type"] == "Course")
        self.assertEqual(course["name"], "AWS Cloud Practitioner")
        self.assertEqual(course["offers"][0]["price"], "4999.00")
        self.assertEqual(course["offers"][0]["priceCurrency"], "INR")
        self.assertEqual(course["hasCourseInstance"][0]["instructor"][0]["name"], "Tara")
        self.assertTrue(any(b["@type"] == "BreadcrumbList" for b in blocks))
        self.assertIn(f'<link rel="canonical" href="https://academy.example.com{self.pub.get_absolute_url()}">', html)
        self.assertIn('<meta property="og:title" content="AWS Cloud Practitioner', html)
        self.assertNotIn('name="robots" content="noindex', html)

    def test_structured_data_cannot_break_out_of_script(self):
        evil = self.make_course(title="</script><script>alert(1)</script>")
        html = self.client.get(evil.get_absolute_url()).content.decode()
        self.assertNotIn("<script>alert(1)</script>", html)
        course = next(b for b in ld_blocks(html) if b["@type"] == "Course")
        self.assertEqual(course["name"], "</script><script>alert(1)</script>")

    def test_home_organization_and_private_pages_noindex(self):
        org = next(b for b in ld_blocks(self.client.get("/").content.decode()) if b["@type"] == "EducationalOrganization")
        self.assertEqual(org["url"], "https://academy.example.com/")
        self.login(self.student)
        for url in (reverse("portal:dashboard"), reverse("accounts:profile")):
            html = self.client.get(url).content.decode()
            self.assertIn('<meta name="robots" content="noindex, nofollow">', html)
            self.assertNotIn('rel="canonical"', html)

    def test_about_and_trainers_pages(self):
        r = self.client.get(reverse("core:trainers"))
        self.assertContains(r, "Tara")
        self.assertContains(r, reverse("core:trainer_detail", args=[self.trainer_profile.pk]))
        r = self.client.get(reverse("core:about"))
        self.assertContains(r, "Meet our trainers")
        self.assertContains(r, "How we teach")  # default copy when About text is empty
        person = next(b for b in ld_blocks(self.client.get(reverse("core:trainer_detail", args=[self.trainer_profile.pk])).content.decode())
                      if b["@type"] == "Person")
        self.assertEqual(person["name"], "Tara")
