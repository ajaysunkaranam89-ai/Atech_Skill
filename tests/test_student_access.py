"""Spec §4E, §6, §11, §15: student access rules, progress, direct URL manipulation."""
from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from apps.courses.models import ContentStatus, CourseStatus, LessonProgress, LessonResource, ResourceType
from apps.enrollments.models import EnrollmentStatus

from .base import LMSTestCase, mp4, pdf


class StudentAccessTests(LMSTestCase):
    def setUp(self):
        super().setUp()
        self.course = self.make_course()
        self.module = self.make_module(self.course)
        self.l1 = self.make_lesson(self.module, title="Published one")
        self.l2 = self.make_lesson(self.module, title="Published two")
        self.draft = self.make_lesson(self.module, status=ContentStatus.DRAFT, title="Secret draft")
        self.enroll(self.student, self.course)

    def test_enrolled_student_sees_published_lessons_only(self):
        self.login(self.student)
        r = self.client.get(reverse("portal:course_home", args=[self.course.slug]))
        self.assertContains(r, "Published one")
        self.assertNotContains(r, "Secret draft")

    def test_student_cannot_access_unpublished_lesson_by_url(self):
        self.login(self.student)
        self.assertEqual(self.client.get(self.draft.get_absolute_url()).status_code, 404)

    def test_unpublished_module_hides_its_lessons(self):
        self.module.status = ContentStatus.UNPUBLISHED
        self.module.save()
        self.login(self.student)
        self.assertEqual(self.client.get(self.l1.get_absolute_url()).status_code, 404)

    def test_student_sees_updated_lesson_after_admin_edit(self):
        self.login(self.owner)
        self.client.post(reverse("dashboard:lesson_edit", args=[self.l1.pk]), {
            "module": self.module.pk, "title": "Published one (2026 edition)", "description": "Fresh content",
            "duration_minutes": 5, "order": 1, "status": "published", "notify": "silent"})
        self.login(self.student)
        r = self.client.get(self.l1.get_absolute_url())
        self.assertContains(r, "Published one (2026 edition)")
        self.assertContains(r, "Fresh content")
        dash = self.client.get(reverse("portal:dashboard"))
        self.assertContains(dash, "Published one (2026 edition)")  # recently updated list

    def test_admin_unpublishes_lesson_then_student_loses_it(self):
        self.login(self.owner)
        self.client.post(reverse("dashboard:lesson_status", args=[self.l1.pk]), {"status": "unpublished"})
        self.login(self.student)
        self.assertEqual(self.client.get(self.l1.get_absolute_url()).status_code, 404)
        self.assertNotContains(self.client.get(reverse("portal:course_home", args=[self.course.slug])), "Published one")

    def test_non_enrolled_student_cannot_access_paid_lesson_or_video(self):
        self.login(self.owner)
        self.client.post(reverse("dashboard:video_replace", args=[self.l1.pk]), {
            "title": "file", "source_type": "upload", "video_file": mp4(), "status": "published", "notify": "silent"})
        self.l1.refresh_from_db()
        asset = self.l1.active_video
        res = LessonResource.objects.create(lesson=self.l1, title="Notes", resource_type=ResourceType.PDF, file=pdf())

        self.login(self.outsider)
        r = self.client.get(self.l1.get_absolute_url())
        self.assertRedirects(r, self.course.get_absolute_url(), fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse("portal:video_stream", args=[asset.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("portal:resource_download", args=[res.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("portal:course_home", args=[self.course.slug])).status_code, 302)
        self.assertEqual(self.client.post(reverse("portal:lesson_complete", args=[self.l1.pk])).status_code, 403)

        self.client.logout()
        self.assertEqual(self.client.get(reverse("portal:video_stream", args=[asset.pk])).status_code, 403)
        r = self.client.get(self.l1.get_absolute_url())
        self.assertIn(reverse("accounts:login"), r["Location"])

        self.login(self.student)
        r = self.client.get(reverse("portal:video_stream", args=[asset.pk]), HTTP_RANGE="bytes=0-99")
        self.assertEqual(r.status_code, 206)
        self.assertEqual(r["Content-Range"].split("/")[0], "bytes 0-99")
        self.assertEqual(self.client.get(reverse("portal:resource_download", args=[res.pk])).status_code, 200)

    def test_unpublished_video_version_not_streamable_by_student(self):
        self.login(self.owner)
        self.client.post(reverse("dashboard:video_replace", args=[self.l1.pk]), {
            "title": "draft file", "source_type": "upload", "video_file": mp4(), "status": "draft", "notify": "silent"})
        self.l1.refresh_from_db()
        self.login(self.student)
        self.assertEqual(self.client.get(reverse("portal:video_stream", args=[self.l1.active_video_id])).status_code, 403)
        self.assertContains(self.client.get(self.l1.get_absolute_url()), "not available yet")

    def test_free_preview_lesson_open_to_everyone(self):
        self.l2.is_free_preview = True
        self.l2.save()
        self.client.logout()
        self.assertEqual(self.client.get(self.l2.get_absolute_url()).status_code, 200)
        self.assertNotEqual(self.client.get(self.l1.get_absolute_url()).status_code, 200)

    def test_expired_and_pending_enrollments_have_no_access(self):
        e = self.student.enrollments.get()
        e.expires_at = timezone.now() - timedelta(days=1)
        e.save()
        self.login(self.student)
        self.assertNotEqual(self.client.get(self.l1.get_absolute_url()).status_code, 200)
        e.expires_at = None
        e.status = EnrollmentStatus.PENDING
        e.save()
        self.assertNotEqual(self.client.get(self.l1.get_absolute_url()).status_code, 200)

    def test_archived_course_keeps_enrolled_access_but_hides_catalogue(self):
        self.course.status = CourseStatus.ARCHIVED
        self.course.save()
        self.login(self.student)
        self.assertEqual(self.client.get(self.course.get_absolute_url()).status_code, 404)
        # lessons are only visible in published courses
        self.assertEqual(self.client.get(self.l1.get_absolute_url()).status_code, 404)

    def test_progress_tracking(self):
        self.login(self.student)
        r = self.client.post(reverse("portal:lesson_complete", args=[self.l1.pk]), {"completed": "1"},
                             HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(r.json()["progress"], {"total": 2, "completed": 1, "percent": 50})
        self.assertTrue(LessonProgress.objects.get(user=self.student, lesson=self.l1).completed)
        r = self.client.post(reverse("api_lesson_progress", args=[self.l2.pk]), {"completed": True}, content_type="application/json")
        self.assertEqual(r.json()["progress"]["percent"], 100)
        r = self.client.post(reverse("api_lesson_progress", args=[self.l2.pk]), {"completed": "false"}, content_type="application/json")
        self.assertEqual(r.json()["progress"]["percent"], 50)
        r = self.client.get(reverse("api_course_progress", args=[self.course.slug]))
        self.assertEqual(r.json()["completed_lessons"], [self.l1.pk])
        dash = self.client.get(reverse("portal:dashboard"))
        self.assertContains(dash, "50%")

    def test_prev_next_navigation(self):
        self.login(self.student)
        r = self.client.get(self.l1.get_absolute_url())
        self.assertEqual(r.context["next_lesson"].pk, self.l2.pk)
        self.assertIsNone(r.context["prev_lesson"])
        r = self.client.get(self.l2.get_absolute_url())
        self.assertEqual(r.context["prev_lesson"].pk, self.l1.pk)
        self.assertIsNone(r.context["next_lesson"])  # draft lesson is skipped

    def test_non_downloadable_resource_blocked(self):
        res = LessonResource.objects.create(lesson=self.l1, title="View only", file=pdf(), is_downloadable=False)
        self.login(self.student)
        self.assertEqual(self.client.get(reverse("portal:resource_download", args=[res.pk])).status_code, 403)

    def test_x_accel_redirect_in_production_mode(self):
        res = LessonResource.objects.create(lesson=self.l1, title="Notes", file=pdf())
        self.login(self.student)
        with self.settings(USE_X_ACCEL_REDIRECT=True):
            r = self.client.get(reverse("portal:resource_download", args=[res.pk]))
        self.assertTrue(r["X-Accel-Redirect"].startswith("/protected/resources/"))
        self.assertEqual(r.content, b"")
