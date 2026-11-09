"""Spec §8 & §15: video update notifications, audiences and de-duplication."""
from django.core import mail
from django.urls import reverse

from apps.courses.models import Batch, BatchStudent, ContentStatus
from apps.notifications.models import Notification, NotificationKind

from .base import LMSTestCase, pdf


class NotificationTests(LMSTestCase):
    def setUp(self):
        super().setUp()
        self.course = self.make_course()
        self.module = self.make_module(self.course)
        self.lesson = self.make_lesson(self.module, title="Terraform")
        self.enroll(self.student, self.course)
        self.enroll(self.outsider, self.course)
        self.batch = Batch.objects.create(course=self.course, name="Nov", code="DEV-NOV", start_date="2026-11-01")
        BatchStudent.objects.create(batch=self.batch, student=self.student)
        self.login(self.owner)

    def replace(self, url="https://youtu.be/bbbbbbbbbbb", notify="all", **extra):
        return self.client.post(reverse("dashboard:video_replace", args=[self.lesson.pk]), {
            "title": "Terraform", "source_type": "youtube", "video_url": url, "status": "published",
            "notify": notify, "notify_email": "on", **extra})

    def test_notify_all_enrolled_on_video_replace(self):
        self.replace()
        self.assertEqual(Notification.objects.filter(kind=NotificationKind.VIDEO_UPDATED).count(), 2)
        self.assertEqual(len(mail.outbox), 2)
        self.assertIn("Lesson video updated", mail.outbox[0].subject)

    def test_notify_selected_batch_only(self):
        self.replace(notify="batches", notify_batches=[self.batch.pk])
        users = set(Notification.objects.values_list("user__email", flat=True))
        self.assertEqual(users, {"student@academy.test"})

    def test_silent_publish(self):
        self.replace(notify="silent")
        self.assertFalse(Notification.objects.exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_no_duplicate_when_same_update_saved_repeatedly(self):
        self.client.post(reverse("dashboard:lesson_edit", args=[self.lesson.pk]), {
            "module": self.module.pk, "title": "Terraform", "description": "IaC basics", "duration_minutes": 0,
            "order": 1, "status": "published", "notify": "all", "notify_email": "on"})
        first = Notification.objects.count()
        self.assertEqual(first, 2)
        for _ in range(3):  # identical saves
            self.client.post(reverse("dashboard:lesson_edit", args=[self.lesson.pk]), {
                "module": self.module.pk, "title": "Terraform", "description": "IaC basics", "duration_minutes": 0,
                "order": 1, "status": "published", "notify": "all", "notify_email": "on"})
        self.assertEqual(Notification.objects.count(), first)
        self.assertEqual(len(mail.outbox), 2)
        # a genuinely new change notifies again
        self.client.post(reverse("dashboard:lesson_edit", args=[self.lesson.pk]), {
            "module": self.module.pk, "title": "Terraform", "description": "IaC basics + modules", "duration_minutes": 0,
            "order": 1, "status": "published", "notify": "all", "notify_email": "on"})
        self.assertEqual(Notification.objects.count(), first + 2)

    def test_new_lesson_publish_notifies(self):
        self.client.post(reverse("dashboard:video_add"), {
            "course": self.course.pk, "module": self.module.pk, "title": "Ansible", "source_type": "youtube",
            "video_url": "https://youtu.be/ccccccccccc", "publish": "published", "notify": "all"})
        self.assertEqual(Notification.objects.filter(kind=NotificationKind.NEW_LESSON).count(), 2)

    def test_draft_lesson_never_notifies(self):
        self.client.post(reverse("dashboard:video_add"), {
            "course": self.course.pk, "module": self.module.pk, "title": "Draft", "source_type": "youtube",
            "video_url": "https://youtu.be/ccccccccccc", "publish": "draft", "notify": "all"})
        self.assertFalse(Notification.objects.exists())

    def test_new_material_notifies(self):
        self.client.post(reverse("dashboard:resource_add"), {
            "lesson": self.lesson.pk, "title": "Terraform notes", "resource_type": "pdf", "file": pdf(),
            "is_downloadable": "on", "is_published": "on", "notify": "all", "notify_email": "on"})
        self.assertEqual(Notification.objects.filter(kind=NotificationKind.NEW_MATERIAL).count(), 2)

    def test_student_dashboard_notifications(self):
        self.replace()
        self.login(self.student)
        r = self.client.get(reverse("notifications:inbox"))
        self.assertContains(r, "Lesson video updated: Terraform")
        n = Notification.objects.get(user=self.student)
        r = self.client.post(reverse("notifications:open", args=[n.pk]))
        self.assertEqual(r["Location"], self.lesson.get_absolute_url())
        n.refresh_from_db()
        self.assertTrue(n.is_read)
        # cannot open someone else's notification
        other = Notification.objects.get(user=self.outsider)
        self.assertEqual(self.client.post(reverse("notifications:open", args=[other.pk])).status_code, 404)

    def test_unenrolled_users_never_notified(self):
        self.lesson.status = ContentStatus.PUBLISHED
        self.replace()
        self.assertFalse(Notification.objects.filter(user=self.trainer).exists())
