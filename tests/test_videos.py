"""Spec §4 & §15: recorded video management, replacement, versions, access."""
from django.urls import reverse

from apps.courses.models import ContentStatus, Lesson, LessonResource, VideoAsset, VideoSource, extract_youtube_id

from .base import LMSTestCase, mp4, pdf, png


class YouTubeParsingTests(LMSTestCase):
    def test_extracts_ids(self):
        for url in [
            "https://youtu.be/dQw4w9WgXcQ", "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=10",
            "https://www.youtube.com/embed/dQw4w9WgXcQ", "https://youtube.com/shorts/dQw4w9WgXcQ",
            "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ",
        ]:
            self.assertEqual(extract_youtube_id(url), "dQw4w9WgXcQ", url)
        for url in ["https://example.com/watch?v=dQw4w9WgXcQ", "https://youtu.be/bad", "javascript:alert(1)", ""]:
            self.assertIsNone(extract_youtube_id(url), url)


class RecordedVideoTests(LMSTestCase):
    def setUp(self):
        super().setUp()
        self.course = self.make_course()
        self.module = self.make_module(self.course)

    def add_video(self, **over):
        data = {
            "course": self.course.pk, "module": self.module.pk, "title": "Intro to Docker",
            "description": "What containers are", "source_type": VideoSource.YOUTUBE,
            "video_url": "https://youtu.be/dQw4w9WgXcQ", "duration_minutes": "12", "order": "",
            "publish": ContentStatus.PUBLISHED, "notify": "silent", "notify_email": "on",
        }
        data.update(over)
        return self.client.post(reverse("dashboard:video_add"), data)

    def test_admin_adds_youtube_lesson(self):
        self.login(self.owner)
        r = self.add_video()
        self.assertEqual(r.status_code, 302)
        lesson = Lesson.objects.get(title="Intro to Docker")
        self.assertEqual(lesson.status, ContentStatus.PUBLISHED)
        self.assertEqual(lesson.active_video.version, 1)
        self.assertEqual(lesson.active_video.embed_url, "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ?rel=0&modestbranding=1")

    def test_admin_uploads_mp4_with_thumbnail_and_pdf(self):
        self.login(self.owner)
        r = self.add_video(source_type=VideoSource.UPLOAD, video_url="", video_file=mp4(), thumbnail=png(), pdf_notes=pdf(),
                           duration_seconds="754")
        self.assertEqual(r.status_code, 302)
        lesson = Lesson.objects.get(title="Intro to Docker")
        v = lesson.active_video
        self.assertTrue(v.video_file.name.startswith(f"videos/lesson_{lesson.pk}/v1_"))
        self.assertTrue(v.thumbnail)
        self.assertEqual(v.duration_display, "12:34")
        self.assertEqual(lesson.resources.get().resource_type, "pdf")

    def test_upload_progress_ajax_returns_json_redirect(self):
        self.login(self.owner)
        r = self.client.post(reverse("dashboard:video_add"), {
            "course": self.course.pk, "module": self.module.pk, "title": "AJAX", "source_type": "upload",
            "video_file": mp4(), "publish": "draft", "notify": "silent",
        }, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(r.status_code, 200)
        self.assertIn("redirect", r.json())

    def test_rejects_fake_mp4_and_bad_urls(self):
        self.login(self.owner)
        from django.core.files.uploadedfile import SimpleUploadedFile

        fake = SimpleUploadedFile("evil.mp4", b"<?php echo 1; ?>" * 10, content_type="video/mp4")
        r = self.add_video(source_type="upload", video_url="", video_file=fake)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "not a valid MP4")
        r = self.add_video(source_type="youtube", video_url="https://vimeo.com/123")
        self.assertContains(r, "recognisable YouTube")
        r = self.add_video(source_type="external", video_url="http://insecure.example.com/v.mp4")
        self.assertContains(r, "HTTPS")
        self.assertFalse(Lesson.objects.exists())

    def test_module_must_match_course(self):
        other = self.make_course(title="Other")
        self.login(self.owner)
        r = self.add_video(course=other.pk)
        self.assertContains(r, "does not belong")

    def test_replace_video_creates_new_version_and_students_see_it(self):
        lesson = self.make_lesson(self.module, title="Kubernetes")
        old = lesson.active_video
        self.enroll(self.student, self.course)
        self.login(self.owner)
        r = self.client.post(reverse("dashboard:video_replace", args=[lesson.pk]), {
            "title": "Kubernetes (re-recorded)", "source_type": "upload", "video_file": mp4("new.mp4"),
            "status": "published", "change_note": "Better audio", "notify": "silent",
        })
        self.assertEqual(r.status_code, 302)
        lesson.refresh_from_db()
        new = lesson.active_video
        self.assertEqual(new.version, 2)
        self.assertTrue(new.is_active)
        old.refresh_from_db()
        self.assertFalse(old.is_active)
        self.assertIsNotNone(old.replaced_at)
        self.assertEqual(VideoAsset.objects.filter(lesson=lesson).count(), 2)  # history kept

        self.login(self.student)
        page = self.client.get(lesson.get_absolute_url())
        self.assertContains(page, new.stream_url())
        self.assertNotContains(page, "dQw4w9WgXcQ")
        # student can stream new file but not the old version id
        self.assertEqual(self.client.get(reverse("portal:video_stream", args=[new.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("portal:video_stream", args=[old.pk])).status_code, 403)

    def test_rollback_to_previous_version(self):
        lesson = self.make_lesson(self.module)
        v1 = lesson.active_video
        self.login(self.owner)
        self.client.post(reverse("dashboard:video_replace", args=[lesson.pk]), {
            "title": "v2", "source_type": "youtube", "video_url": "https://youtu.be/aaaaaaaaaaa", "status": "published", "notify": "silent"})
        self.client.post(reverse("dashboard:video_activate", args=[v1.pk]))
        lesson.refresh_from_db()
        self.assertEqual(lesson.active_video_id, v1.pk)
        self.assertEqual(lesson.videos.filter(is_active=True).count(), 1)

    def test_replace_with_delete_old_file_removes_storage(self):
        self.login(self.owner)
        self.add_video(source_type="upload", video_url="", video_file=mp4())
        lesson = Lesson.objects.get()
        v1 = lesson.active_video
        path = v1.video_file.path
        self.client.post(reverse("dashboard:video_replace", args=[lesson.pk]), {
            "title": "v2", "source_type": "upload", "video_file": mp4("v2.mp4"), "status": "published",
            "delete_old_file": "on", "notify": "silent"})
        import os

        self.assertFalse(os.path.exists(path))
        v1.refresh_from_db()
        self.assertFalse(v1.video_file)

    def test_admin_can_preview_unpublished(self):
        lesson = self.make_lesson(self.module, status=ContentStatus.DRAFT)
        self.login(self.owner)
        self.assertEqual(self.client.get(reverse("dashboard:lesson_preview", args=[lesson.pk])).status_code, 200)
        r = self.client.get(lesson.get_absolute_url())
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Admin preview")

    def test_updating_pdf_notes_replaces_file(self):
        lesson = self.make_lesson(self.module)
        self.login(self.owner)
        self.client.post(reverse("dashboard:resource_add"), {
            "lesson": lesson.pk, "title": "Cheatsheet", "resource_type": "pdf", "file": pdf("a.pdf"),
            "is_downloadable": "on", "is_published": "on", "notify": "silent"})
        res = LessonResource.objects.get()
        first = res.file.name
        self.client.post(reverse("dashboard:resource_edit", args=[res.pk]), {
            "lesson": lesson.pk, "title": "Cheatsheet v2", "resource_type": "pdf", "file": pdf("b.pdf"),
            "is_downloadable": "on", "is_published": "on", "notify": "silent"})
        res.refresh_from_db()
        self.assertNotEqual(res.file.name, first)
        self.assertEqual(res.version, 2)
