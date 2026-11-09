import shutil
import tempfile

from django.core import mail
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from apps.accounts.models import Role, TrainerProfile, User
from apps.courses.models import ContentStatus, Course, CourseModule, CourseStatus, Lesson, VideoAsset, VideoSource
from apps.enrollments.models import Enrollment, EnrollmentSource, EnrollmentStatus

TEST_MEDIA = tempfile.mkdtemp(prefix="ts_media_")
TEST_PRIVATE = tempfile.mkdtemp(prefix="ts_private_")

MP4_BYTES = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + b"\x00" * 2048
PDF_BYTES = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"
def _make_png():
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (0, 180, 216)).save(buf, format="PNG")
    return buf.getvalue()


PNG_BYTES = _make_png()


def mp4(name="lesson.mp4"):
    return SimpleUploadedFile(name, MP4_BYTES, content_type="video/mp4")


def pdf(name="notes.pdf"):
    return SimpleUploadedFile(name, PDF_BYTES, content_type="application/pdf")


def png(name="thumb.png"):
    return SimpleUploadedFile(name, PNG_BYTES, content_type="image/png")


@override_settings(
    MEDIA_ROOT=TEST_MEDIA,
    PRIVATE_MEDIA_ROOT=TEST_PRIVATE,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
    RAZORPAY_KEY_ID="rzp_test_key",
    RAZORPAY_KEY_SECRET="test_secret",
    RAZORPAY_WEBHOOK_SECRET="whsec_test",
    ALLOWED_HOSTS=["testserver"],
    USE_X_ACCEL_REDIRECT=False,
)
class LMSTestCase(TestCase):
    password = "Str0ng!Passw0rd"

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEST_MEDIA, ignore_errors=True)
        shutil.rmtree(TEST_PRIVATE, ignore_errors=True)

    def setUp(self):
        cache.clear()
        mail.outbox = []
        self.owner = User.objects.create_superuser("owner@academy.test", self.password, first_name="Owner")
        self.course_admin = User.objects.create_user("cadmin@academy.test", self.password, role=Role.COURSE_ADMIN)
        self.trainer = User.objects.create_user("trainer@academy.test", self.password, role=Role.TRAINER, first_name="Tara")
        self.trainer_profile = TrainerProfile.objects.create(user=self.trainer, designation="DevOps Engineer")
        self.other_trainer = User.objects.create_user("trainer2@academy.test", self.password, role=Role.TRAINER)
        TrainerProfile.objects.create(user=self.other_trainer)
        self.student = User.objects.create_user("student@academy.test", self.password, first_name="Sam")
        self.outsider = User.objects.create_user("outsider@academy.test", self.password, first_name="Olly")

    # ---------- helpers
    def login(self, user):
        self.client.force_login(user)

    def make_course(self, status=CourseStatus.PUBLISHED, price=4999, title="DevOps Bootcamp"):
        c = Course.objects.create(
            title=title, short_description="Learn DevOps", description="Full description", price=price, status=status
        )
        c.trainers.add(self.trainer_profile)
        return c

    def make_module(self, course, status=ContentStatus.PUBLISHED, title="Module 1"):
        return CourseModule.objects.create(course=course, title=title, status=status)

    def make_lesson(self, module, status=ContentStatus.PUBLISHED, title="Lesson 1", video_url="https://youtu.be/dQw4w9WgXcQ", **kw):
        lesson = Lesson.objects.create(module=module, title=title, status=status, **kw)
        if video_url:
            v = VideoAsset.objects.create(
                lesson=lesson, title=title, source_type=VideoSource.YOUTUBE, video_url=video_url,
                status=ContentStatus.PUBLISHED if status == ContentStatus.PUBLISHED else ContentStatus.DRAFT,
            )
            lesson.active_video = v
            lesson.save()
        return lesson

    def enroll(self, user, course, status=EnrollmentStatus.ACTIVE):
        e = Enrollment.objects.create(student=user, course=course, status=status, source=EnrollmentSource.ADMIN)
        return e
