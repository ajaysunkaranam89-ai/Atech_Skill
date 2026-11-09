"""Spec §15: admin creates/updates/publishes courses, modules and lessons; RBAC."""
import json

from django.urls import reverse

from apps.core.models import AuditLog
from apps.courses.models import ContentStatus, Course, CourseModule, CourseStatus, Lesson
from apps.enrollments.models import Enrollment

from .base import LMSTestCase, png


class CourseManagementTests(LMSTestCase):
    def course_payload(self, **over):
        data = {
            "title": "Python Full Stack", "short_description": "Python + Django", "description": "Long text",
            "price": "9999", "discounted_price": "7999", "duration_text": "12 weeks", "duration_hours": "60",
            "level": "beginner", "mode": "hybrid", "language": "English", "syllabus": "Python\nDjango\nDeploy",
            "learning_objectives": "Build apps\nDeploy apps", "prerequisites": "Basic computer skills",
            "target_audience": "Freshers", "trainers": [self.trainer_profile.pk], "access_days": "365",
            "status": CourseStatus.DRAFT, "certificate_enabled": "on",
        }
        data.update(over)
        return data

    def test_admin_creates_course(self):
        self.login(self.owner)
        r = self.client.post(reverse("dashboard:course_add"), {**self.course_payload(), "thumbnail": png()})
        self.assertEqual(r.status_code, 302)
        c = Course.objects.get(title="Python Full Stack")
        self.assertEqual(c.slug, "python-full-stack")
        self.assertEqual(c.status, CourseStatus.DRAFT)
        self.assertEqual(c.effective_price, 7999)
        self.assertTrue(c.thumbnail.name.startswith("courses/"))
        self.assertEqual(c.syllabus_list, ["Python", "Django", "Deploy"])
        self.assertTrue(AuditLog.objects.filter(action="create", object_type="course").exists())

    def test_discount_must_be_lower_than_price(self):
        self.login(self.owner)
        r = self.client.post(reverse("dashboard:course_add"), self.course_payload(discounted_price="20000"))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Course.objects.exists())

    def test_admin_updates_course_and_site_shows_latest(self):
        c = self.make_course()
        self.login(self.course_admin)
        r = self.client.post(reverse("dashboard:course_edit", args=[c.pk]),
                             self.course_payload(title="DevOps Bootcamp 2.0", price="5999", discounted_price="",
                                                 status=CourseStatus.PUBLISHED, short_description="Updated blurb"))
        self.assertEqual(r.status_code, 302, getattr(r, "context", None) and r.context["form"].errors)
        c.refresh_from_db()
        self.assertEqual(c.title, "DevOps Bootcamp 2.0")
        page = self.client.get(c.get_absolute_url())
        self.assertContains(page, "DevOps Bootcamp 2.0")
        self.assertContains(page, "Updated blurb")
        self.assertContains(page, "₹5,999")

    def test_publish_unpublish_archive_controls_catalogue(self):
        c = self.make_course(status=CourseStatus.DRAFT)
        self.client.logout()
        self.assertNotContains(self.client.get(reverse("core:course_list")), c.title)
        self.assertEqual(self.client.get(c.get_absolute_url()).status_code, 404)

        self.login(self.owner)
        self.client.post(reverse("dashboard:course_status", args=[c.pk]), {"action": "publish"})
        c.refresh_from_db()
        self.assertEqual(c.status, CourseStatus.PUBLISHED)
        self.assertIsNotNone(c.published_at)
        self.client.logout()
        self.assertContains(self.client.get(reverse("core:course_list")), c.title)

        for action, status in [("unpublish", CourseStatus.UNPUBLISHED), ("archive", CourseStatus.ARCHIVED)]:
            self.login(self.owner)
            self.client.post(reverse("dashboard:course_status", args=[c.pk]), {"action": action})
            c.refresh_from_db()
            self.assertEqual(c.status, status)
            self.client.logout()
            self.assertNotContains(self.client.get(reverse("core:course_list")), c.title)
            self.assertEqual(self.client.get(c.get_absolute_url()).status_code, 404)

    def test_delete_course_requires_typed_confirmation_and_keeps_enrollments(self):
        c = self.make_course()
        self.enroll(self.student, c)
        self.login(self.owner)
        url = reverse("dashboard:course_delete", args=[c.pk])
        self.assertContains(self.client.get(url), "Type")
        self.client.post(url, {"confirm_text": "wrong"})
        self.assertTrue(Course.objects.filter(pk=c.pk).exists())
        self.client.post(url, {"confirm_text": c.title})
        self.assertFalse(Course.objects.filter(pk=c.pk).exists())
        e = Enrollment.objects.get(student=self.student)
        self.assertIsNone(e.course)
        self.assertEqual(e.course_title, "DevOps Bootcamp")

    def test_course_admin_cannot_delete_course(self):
        c = self.make_course()
        self.login(self.course_admin)
        self.assertEqual(self.client.get(reverse("dashboard:course_delete", args=[c.pk])).status_code, 403)

    def test_student_cannot_open_dashboard(self):
        self.login(self.student)
        self.assertEqual(self.client.get(reverse("dashboard:overview")).status_code, 403)
        self.assertEqual(self.client.get(reverse("dashboard:course_add")).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(reverse("dashboard:overview")).status_code, 302)

    def test_list_search_filter_sort_pagination(self):
        for i in range(25):
            self.make_course(title=f"Course {i:02d}", status=CourseStatus.PUBLISHED if i % 2 else CourseStatus.DRAFT)
        self.login(self.owner)
        r = self.client.get(reverse("dashboard:courses"), {"q": "Course 1", "sort": "title"})
        self.assertEqual(r.context["total"], 10)
        r = self.client.get(reverse("dashboard:courses"), {"status": "draft"})
        self.assertEqual(r.context["total"], 13)
        r = self.client.get(reverse("dashboard:courses"), {"page": 2, "sort": "title"})
        self.assertEqual(len(r.context["rows"]), 5)
        self.assertEqual(self.client.get(reverse("dashboard:courses"), {"category_id": "abc"}).status_code, 200)


class ModuleLessonTests(LMSTestCase):
    def test_admin_creates_modules_and_orders_them(self):
        c = self.make_course()
        self.login(self.owner)
        for t in ["Linux", "Git", "Docker"]:
            self.client.post(reverse("dashboard:module_add"), {"course": c.pk, "title": t, "status": "published", "order": ""})
        mods = list(c.modules.order_by("order").values_list("title", flat=True))
        self.assertEqual(mods, ["Linux", "Git", "Docker"])

    def test_trainer_scoped_to_assigned_courses(self):
        mine = self.make_course(title="Mine")
        other = Course.objects.create(title="Other", short_description="x", description="x", price=1)
        self.login(self.trainer)
        r = self.client.get(reverse("dashboard:courses"))
        self.assertContains(r, "Mine")
        self.assertNotContains(r, ">Other<")
        self.assertEqual(self.client.get(reverse("dashboard:course_builder", args=[other.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("dashboard:course_builder", args=[mine.pk])).status_code, 200)
        # trainers cannot edit course metadata
        self.assertEqual(self.client.get(reverse("dashboard:course_edit", args=[mine.pk])).status_code, 403)
        # but can add modules to their course and not to others
        self.client.post(reverse("dashboard:module_add"), {"course": other.pk, "title": "X", "status": "draft"})
        self.assertFalse(CourseModule.objects.filter(course=other).exists())
        self.client.post(reverse("dashboard:module_add"), {"course": mine.pk, "title": "Y", "status": "draft"})
        self.assertTrue(CourseModule.objects.filter(course=mine, title="Y").exists())

    def test_drag_and_drop_reorder(self):
        c = self.make_course()
        m1, m2 = self.make_module(c, title="A"), self.make_module(c, title="B")
        l1, l2, l3 = (self.make_lesson(m1, title=t) for t in ["L1", "L2", "L3"])
        self.login(self.owner)
        url = reverse("dashboard:course_reorder", args=[c.pk])
        payload = {"modules": [m2.pk, m1.pk], "lessons": {str(m1.pk): [l3.pk, l1.pk], str(m2.pk): [l2.pk]}}
        r = self.client.post(url, json.dumps(payload), content_type="application/json")
        self.assertEqual(r.json(), {"ok": True})
        m1.refresh_from_db(); m2.refresh_from_db(); l2.refresh_from_db(); l3.refresh_from_db()
        self.assertEqual((m2.order, m1.order), (1, 2))
        self.assertEqual(l2.module_id, m2.pk)
        self.assertEqual(l3.order, 1)
        # foreign lesson ids are rejected
        other = self.make_course(title="Other")
        foreign = self.make_lesson(self.make_module(other))
        bad = {"modules": [m1.pk], "lessons": {str(m1.pk): [foreign.pk]}}
        r = self.client.post(url, json.dumps(bad), content_type="application/json")
        self.assertEqual(r.status_code, 400)
        foreign.refresh_from_db()
        self.assertNotEqual(foreign.module_id, m1.pk)

    def test_unpublish_lesson(self):
        c = self.make_course()
        lesson = self.make_lesson(self.make_module(c))
        self.login(self.owner)
        self.client.post(reverse("dashboard:lesson_status", args=[lesson.pk]), {"status": "unpublished"})
        lesson.refresh_from_db()
        self.assertEqual(lesson.status, ContentStatus.UNPUBLISHED)
        self.assertEqual(lesson.active_video.status, ContentStatus.UNPUBLISHED)

    def test_delete_lesson_requires_confirmation(self):
        c = self.make_course()
        lesson = self.make_lesson(self.make_module(c), title="Docker basics")
        self.enroll(self.student, c)
        self.login(self.owner)
        url = reverse("dashboard:lesson_delete", args=[lesson.pk])
        self.client.post(url, {"confirm_text": "nope"})
        self.assertTrue(Lesson.objects.filter(pk=lesson.pk).exists())
        self.client.post(url, {"confirm_text": "Docker basics"})
        self.assertFalse(Lesson.objects.filter(pk=lesson.pk).exists())
        self.assertTrue(Enrollment.objects.filter(student=self.student, course=c).exists())


class InlineCategoryTests(LMSTestCase):
    def test_create_category_from_course_form(self):
        from apps.courses.models import CourseCategory

        self.login(self.owner)
        base = {"title": "AWS Basics", "short_description": "x", "description": "x", "price": "0", "level": "all",
                "mode": "hybrid", "language": "English", "access_days": "0", "duration_hours": "0", "status": "draft"}
        r = self.client.get(reverse("dashboard:course_add"))
        self.assertContains(r, "No categories yet")
        self.client.post(reverse("dashboard:course_add"), {**base, "new_category": "Cloud"})
        self.assertEqual(Course.objects.get(title="AWS Basics").category.name, "Cloud")
        # existing name (case-insensitive) is reused, not duplicated
        self.client.post(reverse("dashboard:course_add"), {**base, "title": "Azure Basics", "new_category": "cloud"})
        self.assertEqual(CourseCategory.objects.count(), 1)
        self.assertEqual(Course.objects.get(title="Azure Basics").category.name, "Cloud")
