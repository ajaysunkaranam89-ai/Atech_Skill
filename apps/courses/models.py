import hashlib
from datetime import timedelta
import re
from urllib.parse import parse_qs, urlparse

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify

from apps.core.models import TimeStampedModel, private_storage
from apps.core.validators import (
    validate_document_upload,
    validate_image_upload,
    validate_video_upload,
)


def unique_slug(instance, value, field="slug"):
    base = slugify(value)[:200] or "item"
    slug, n = base, 2
    Model = type(instance)
    while Model.objects.filter(**{field: slug}).exclude(pk=instance.pk).exists():
        slug = f"{base}-{n}"
        n += 1
    return slug


class CourseCategory(TimeStampedModel):
    name = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(max_length=140, unique=True, blank=True)
    description = models.TextField(blank=True)
    icon = models.CharField(max_length=60, blank=True, help_text="Bootstrap icon name, e.g. bi-cloud")
    order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["order", "name"]
        verbose_name_plural = "Course categories"

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slug(self, self.name)
        super().save(*args, **kwargs)


class CourseStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    PUBLISHED = "published", "Published"
    UNPUBLISHED = "unpublished", "Unpublished"
    ARCHIVED = "archived", "Archived"


class ContentStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    PUBLISHED = "published", "Published"
    UNPUBLISHED = "unpublished", "Unpublished"


class Level(models.TextChoices):
    BEGINNER = "beginner", "Beginner"
    INTERMEDIATE = "intermediate", "Intermediate"
    ADVANCED = "advanced", "Advanced"
    ALL = "all", "All levels"


class Mode(models.TextChoices):
    LIVE = "live", "Live online (Google Meet)"
    RECORDED = "recorded", "Self-paced recorded"
    HYBRID = "hybrid", "Live + recorded"


class CourseQuerySet(models.QuerySet):
    def published(self):
        return self.filter(status=CourseStatus.PUBLISHED)

    def with_next_batch(self):
        """Annotate `next_batch_date`: earliest open upcoming/ongoing batch start (for course cards)."""
        from django.db.models import Min, Q

        return self.annotate(next_batch_date=Min(
            "batches__start_date",
            filter=Q(batches__is_open_for_enrollment=True, batches__status__in=["upcoming", "ongoing"]),
        ))

    def managed_by(self, user):
        if user.can_manage_all_content:
            return self
        if user.is_trainer:
            return self.filter(trainers__user=user).distinct()
        return self.none()


class Course(TimeStampedModel):
    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True, blank=True)
    category = models.ForeignKey(CourseCategory, null=True, blank=True, on_delete=models.SET_NULL, related_name="courses")
    short_description = models.CharField(max_length=300)
    description = models.TextField(help_text="Full course overview shown on the course page")
    thumbnail = models.ImageField(upload_to="courses/", blank=True, validators=[validate_image_upload])
    promo_video_url = models.URLField(blank=True, help_text="Optional public YouTube preview")

    price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    discounted_price = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True, help_text="Leave blank for no discount"
    )

    duration_text = models.CharField(max_length=80, blank=True, help_text="e.g. 8 weeks / 40 hours")
    duration_hours = models.PositiveIntegerField(default=0)
    level = models.CharField(max_length=20, choices=Level.choices, default=Level.ALL)
    mode = models.CharField(max_length=20, choices=Mode.choices, default=Mode.HYBRID)
    language = models.CharField(max_length=60, default="English")

    syllabus = models.TextField(blank=True, help_text="Overview syllabus. One topic per line.")
    learning_objectives = models.TextField(blank=True, help_text="One objective per line")
    prerequisites = models.TextField(blank=True, help_text="One prerequisite per line")
    target_audience = models.TextField(blank=True, help_text="One item per line")

    trainers = models.ManyToManyField("accounts.TrainerProfile", blank=True, related_name="courses")
    access_days = models.PositiveIntegerField(
        default=0, help_text="Days of access after enrollment. 0 = lifetime access."
    )
    is_featured = models.BooleanField(default=False)
    certificate_enabled = models.BooleanField(default=True)
    auto_issue_certificate = models.BooleanField(
        default=True,
        help_text="Issue the certificate automatically when a student completes every published lesson",
    )

    seo_title = models.CharField(
        "SEO title", max_length=70, blank=True,
        help_text="Shown in Google results (max ~60 characters). e.g. 'AWS Cloud Training in Bangalore | Online Live Classes'",
    )
    seo_description = models.CharField(
        "SEO description", max_length=170, blank=True,
        help_text="Google snippet (max ~155 characters). Mention the skill, format, location and a benefit.",
    )
    status = models.CharField(max_length=20, choices=CourseStatus.choices, default=CourseStatus.DRAFT, db_index=True)
    published_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    objects = CourseQuerySet.as_manager()

    class Meta:
        ordering = ["-is_featured", "title"]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slug(self, self.title)
        if self.status == CourseStatus.PUBLISHED and not self.published_at:
            self.published_at = timezone.now()
        super().save(*args, **kwargs)

    def clean(self):
        if self.discounted_price is not None and self.discounted_price >= self.price:
            raise ValidationError({"discounted_price": "Discounted price must be lower than the regular price."})
        if self.discounted_price is not None and self.discounted_price < 0:
            raise ValidationError({"discounted_price": "Price cannot be negative."})

    def get_absolute_url(self):
        return reverse("core:course_detail", args=[self.slug])

    @property
    def effective_price(self):
        return self.discounted_price if self.discounted_price is not None else self.price

    @property
    def promo_embed_url(self):
        yid = extract_youtube_id(self.promo_video_url)
        return f"https://www.youtube-nocookie.com/embed/{yid}?rel=0" if yid else None

    @property
    def is_free(self):
        return self.effective_price == 0

    @property
    def is_published(self):
        return self.status == CourseStatus.PUBLISHED

    @property
    def discount_percent(self):
        if self.discounted_price is None or not self.price:
            return 0
        return int(round((1 - self.discounted_price / self.price) * 100))

    def _lines(self, text):
        return [line.strip(" -•\t") for line in (text or "").splitlines() if line.strip()]

    @property
    def syllabus_list(self):
        return self._lines(self.syllabus)

    @property
    def objectives_list(self):
        return self._lines(self.learning_objectives)

    @property
    def prerequisites_list(self):
        return self._lines(self.prerequisites)

    @property
    def audience_list(self):
        return self._lines(self.target_audience)

    def published_lessons(self):
        return Lesson.objects.filter(
            module__course=self,
            module__status=ContentStatus.PUBLISHED,
            status=ContentStatus.PUBLISHED,
        )

    def published_modules(self):
        return self.modules.filter(status=ContentStatus.PUBLISHED)


class CourseModule(TimeStampedModel):
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="modules")
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    order = models.PositiveIntegerField(default=0, db_index=True)
    status = models.CharField(max_length=20, choices=ContentStatus.choices, default=ContentStatus.DRAFT)

    class Meta:
        ordering = ["course", "order", "id"]

    def __str__(self):
        return f"{self.course.title} › {self.title}"

    def save(self, *args, **kwargs):
        if not self.pk and not self.order:
            last = CourseModule.objects.filter(course=self.course).aggregate(m=models.Max("order"))["m"]
            self.order = (last or 0) + 1
        super().save(*args, **kwargs)


class LessonQuerySet(models.QuerySet):
    def visible_to_students(self):
        return self.filter(
            status=ContentStatus.PUBLISHED,
            module__status=ContentStatus.PUBLISHED,
            module__course__status=CourseStatus.PUBLISHED,
        )


class Lesson(TimeStampedModel):
    module = models.ForeignKey(CourseModule, on_delete=models.CASCADE, related_name="lessons")
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    duration_minutes = models.PositiveIntegerField(default=0)
    order = models.PositiveIntegerField(default=0, db_index=True)
    status = models.CharField(max_length=20, choices=ContentStatus.choices, default=ContentStatus.DRAFT, db_index=True)
    is_free_preview = models.BooleanField(
        default=False, help_text="Allow anyone (even non-enrolled visitors) to watch this lesson"
    )
    active_video = models.ForeignKey(
        "VideoAsset", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    published_at = models.DateTimeField(null=True, blank=True)
    # Bumped whenever students would notice a change (new video, content edit, new material).
    content_updated_at = models.DateTimeField(default=timezone.now, db_index=True)

    objects = LessonQuerySet.as_manager()

    class Meta:
        ordering = ["module__order", "order", "id"]

    def __str__(self):
        return self.title

    @property
    def course(self):
        return self.module.course

    def save(self, *args, **kwargs):
        if not self.pk and not self.order:
            last = Lesson.objects.filter(module=self.module).aggregate(m=models.Max("order"))["m"]
            self.order = (last or 0) + 1
        if self.status == ContentStatus.PUBLISHED and not self.published_at:
            self.published_at = timezone.now()
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("portal:lesson", args=[self.module.course.slug, self.pk])

    @property
    def is_visible_to_students(self):
        return (
            self.status == ContentStatus.PUBLISHED
            and self.module.status == ContentStatus.PUBLISHED
            and self.module.course.status == CourseStatus.PUBLISHED
        )

    @property
    def playable_video(self):
        """The video students may watch: the active version, only if published."""
        v = self.active_video
        if v and v.status == ContentStatus.PUBLISHED:
            return v
        return None

    def content_fingerprint(self):
        """Stable hash of student-visible content; used to de-duplicate notifications."""
        v = self.active_video
        parts = [
            self.title,
            self.description,
            str(self.status),
            f"{v.pk}:{v.version}:{v.status}" if v else "-",
            ",".join(str(r.pk) for r in self.resources.filter(is_published=True).order_by("pk")),
        ]
        return hashlib.sha256("|".join(parts).encode()).hexdigest()[:20]


class VideoSource(models.TextChoices):
    YOUTUBE = "youtube", "YouTube (unlisted / watch URL)"
    YOUTUBE_EMBED = "youtube_embed", "YouTube embed URL"
    EXTERNAL = "external", "External HTTPS video URL"
    UPLOAD = "upload", "Uploaded MP4 file"


YOUTUBE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def extract_youtube_id(url):
    if not url:
        return None
    u = urlparse(url.strip())
    host = (u.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    vid = None
    if host == "youtu.be":
        vid = u.path.lstrip("/").split("/")[0]
    elif host in {"youtube.com", "youtube-nocookie.com", "music.youtube.com"}:
        if u.path == "/watch":
            vid = parse_qs(u.query).get("v", [None])[0]
        else:
            parts = [p for p in u.path.split("/") if p]
            if len(parts) >= 2 and parts[0] in {"embed", "shorts", "live", "v"}:
                vid = parts[1]
    if vid and YOUTUBE_ID_RE.match(vid):
        return vid
    return None


def video_upload_path(instance, filename):
    ext = filename.rsplit(".", 1)[-1].lower()
    stamp = timezone.now().strftime("%Y%m%d%H%M%S")
    return f"videos/lesson_{instance.lesson_id}/v{instance.version}_{stamp}.{ext}"


class VideoAsset(TimeStampedModel):
    """One version of a lesson's recorded video. Replacing a video creates a new version."""

    lesson = models.ForeignKey(Lesson, on_delete=models.CASCADE, related_name="videos")
    title = models.CharField(max_length=200)
    source_type = models.CharField(max_length=20, choices=VideoSource.choices)
    video_url = models.URLField(max_length=500, blank=True)
    video_file = models.FileField(
        upload_to=video_upload_path, storage=private_storage, blank=True, validators=[validate_video_upload]
    )
    thumbnail = models.ImageField(upload_to="video_thumbs/", blank=True, validators=[validate_image_upload])
    duration_seconds = models.PositiveIntegerField(default=0)
    version = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=20, choices=ContentStatus.choices, default=ContentStatus.DRAFT)
    is_active = models.BooleanField(default=True, help_text="The version currently attached to the lesson")
    replaced_at = models.DateTimeField(null=True, blank=True)
    change_note = models.CharField(max_length=255, blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ["lesson", "-version"]
        constraints = [models.UniqueConstraint(fields=["lesson", "version"], name="uniq_video_version_per_lesson")]

    def __str__(self):
        return f"{self.title} (v{self.version})"

    def clean(self):
        st = self.source_type
        if st == VideoSource.UPLOAD:
            if not self.video_file:
                raise ValidationError({"video_file": "Upload an MP4 file for this source type."})
        else:
            if not self.video_url:
                raise ValidationError({"video_url": "Enter the video URL for this source type."})
            if not self.video_url.startswith("https://"):
                raise ValidationError({"video_url": "Video URLs must use HTTPS."})
            if st in (VideoSource.YOUTUBE, VideoSource.YOUTUBE_EMBED) and not extract_youtube_id(self.video_url):
                raise ValidationError({"video_url": "This is not a recognisable YouTube video URL."})

    @property
    def youtube_id(self):
        if self.source_type in (VideoSource.YOUTUBE, VideoSource.YOUTUBE_EMBED):
            return extract_youtube_id(self.video_url)
        return None

    @property
    def embed_url(self):
        yid = self.youtube_id
        if yid:
            return f"https://www.youtube-nocookie.com/embed/{yid}?rel=0&modestbranding=1&enablejsapi=1&fs=0&playsinline=1"
        return None

    @property
    def is_file(self):
        return self.source_type == VideoSource.UPLOAD

    def stream_url(self):
        # Version in querystring busts browser caches when a video is replaced.
        return reverse("portal:video_stream", args=[self.pk]) + f"?v={self.version}"

    @property
    def duration_display(self):
        s = self.duration_seconds
        if not s:
            return ""
        h, rem = divmod(s, 3600)
        m, sec = divmod(rem, 60)
        return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


class ResourceType(models.TextChoices):
    PDF = "pdf", "PDF notes"
    FILE = "file", "Downloadable file"
    LINK = "link", "External link"


class LessonResource(TimeStampedModel):
    lesson = models.ForeignKey(Lesson, on_delete=models.CASCADE, related_name="resources")
    title = models.CharField(max_length=200)
    resource_type = models.CharField(max_length=10, choices=ResourceType.choices, default=ResourceType.PDF)
    file = models.FileField(
        upload_to="resources/%Y/%m/", storage=private_storage, blank=True, validators=[validate_document_upload]
    )
    url = models.URLField(blank=True)
    description = models.CharField(max_length=300, blank=True)
    is_downloadable = models.BooleanField(default=True, help_text="Allow enrolled students to download")
    is_published = models.BooleanField(default=True)
    order = models.PositiveIntegerField(default=0)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["lesson", "order", "id"]

    def __str__(self):
        return self.title

    def clean(self):
        if self.resource_type == ResourceType.LINK:
            if not self.url:
                raise ValidationError({"url": "Enter a URL for link resources."})
        elif not self.file:
            raise ValidationError({"file": "Upload a file for this resource."})
        if self.resource_type == ResourceType.PDF and self.file and not self.file.name.lower().endswith(".pdf"):
            raise ValidationError({"file": "PDF notes must be a .pdf file."})

    def download_url(self):
        if self.resource_type == ResourceType.LINK:
            return self.url
        return reverse("portal:resource_download", args=[self.pk])


class LessonProgress(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="lesson_progress")
    lesson = models.ForeignKey(Lesson, on_delete=models.CASCADE, related_name="progress")
    completed = models.BooleanField(default=False)
    completed_at = models.DateTimeField(null=True, blank=True)
    last_viewed_at = models.DateTimeField(auto_now=True)
    watched_video_version = models.PositiveIntegerField(default=0)
    position_seconds = models.PositiveIntegerField(default=0, help_text="Where the student stopped (resume point)")
    furthest_seconds = models.PositiveIntegerField(default=0, help_text="Furthest point reached by continuous watching")
    watched_percent = models.PositiveSmallIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "lesson"], name="uniq_progress_user_lesson")]

    def __str__(self):
        return f"{self.user} – {self.lesson} – {'done' if self.completed else 'viewed'}"


class Assignment(TimeStampedModel):
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="assignments")
    lesson = models.ForeignKey(Lesson, null=True, blank=True, on_delete=models.SET_NULL, related_name="assignments")
    title = models.CharField(max_length=200)
    instructions = models.TextField()
    attachment = models.FileField(
        upload_to="assignments/briefs/", storage=private_storage, blank=True, validators=[validate_document_upload]
    )
    due_at = models.DateTimeField(null=True, blank=True)
    max_marks = models.PositiveIntegerField(default=100)
    is_published = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ["course", "due_at", "id"]

    def __str__(self):
        return f"{self.course} – {self.title}"

    def clean(self):
        if self.lesson_id and self.lesson.module.course_id != self.course_id:
            raise ValidationError({"lesson": "Lesson must belong to the selected course."})

    @property
    def is_overdue(self):
        return bool(self.due_at and self.due_at < timezone.now())


class SubmissionStatus(models.TextChoices):
    SUBMITTED = "submitted", "Submitted"
    GRADED = "graded", "Graded"
    RESUBMIT = "resubmit", "Resubmission requested"


class AssignmentSubmission(models.Model):
    assignment = models.ForeignKey(Assignment, on_delete=models.CASCADE, related_name="submissions")
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="submissions")
    text_answer = models.TextField(blank=True)
    file = models.FileField(
        upload_to="assignments/submissions/%Y/%m/", storage=private_storage, blank=True,
        validators=[validate_document_upload],
    )
    link = models.URLField(blank=True, help_text="e.g. GitHub repository")
    status = models.CharField(max_length=20, choices=SubmissionStatus.choices, default=SubmissionStatus.SUBMITTED)
    marks = models.PositiveIntegerField(null=True, blank=True)
    feedback = models.TextField(blank=True)
    graded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    graded_at = models.DateTimeField(null=True, blank=True)
    submitted_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-submitted_at"]
        constraints = [
            models.UniqueConstraint(fields=["assignment", "student"], name="uniq_submission_per_student")
        ]

    def __str__(self):
        return f"{self.student} – {self.assignment.title}"

    def clean(self):
        if not (self.text_answer or self.file or self.link):
            raise ValidationError("Provide an answer, a file or a link.")
        if self.marks is not None and self.marks > self.assignment.max_marks:
            raise ValidationError({"marks": f"Marks cannot exceed {self.assignment.max_marks}."})


class BatchStatus(models.TextChoices):
    UPCOMING = "upcoming", "Upcoming"
    ONGOING = "ongoing", "Ongoing"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"


class Batch(TimeStampedModel):
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="batches")
    name = models.CharField(max_length=150)
    code = models.CharField(max_length=40, unique=True)
    trainer = models.ForeignKey(
        "accounts.TrainerProfile", null=True, blank=True, on_delete=models.SET_NULL, related_name="batches"
    )
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    schedule = models.CharField(max_length=200, blank=True, help_text="e.g. Mon/Wed/Fri 7–9 PM IST")
    capacity = models.PositiveIntegerField(default=30)
    status = models.CharField(max_length=20, choices=BatchStatus.choices, default=BatchStatus.UPCOMING)
    is_open_for_enrollment = models.BooleanField(default=True)
    students = models.ManyToManyField(settings.AUTH_USER_MODEL, through="BatchStudent", related_name="batches")

    class Meta:
        ordering = ["-start_date"]
        verbose_name_plural = "Batches"

    def __str__(self):
        return f"{self.code} – {self.name}"

    def clean(self):
        if self.end_date and self.start_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "End date cannot be before start date."})

    @property
    def seats_taken(self):
        return self.batch_students.count()

    @property
    def seats_left(self):
        return max(self.capacity - self.seats_taken, 0)

    @property
    def is_full(self):
        return self.seats_left == 0

    def add_student(self, student, force=False):
        """Add a student if a seat is free. Returns True if the student is (now) in the batch.

        Locks the batch row so two simultaneous payments cannot take the last seat twice.
        `force=True` lets an administrator exceed capacity deliberately.
        """
        from django.db import transaction

        with transaction.atomic():
            batch = Batch.objects.select_for_update().get(pk=self.pk)
            if BatchStudent.objects.filter(batch=batch, student=student).exists():
                return True
            if not force and batch.batch_students.count() >= batch.capacity:
                return False
            BatchStudent.objects.create(batch=batch, student=student)
            return True


class BatchStudent(models.Model):
    batch = models.ForeignKey(Batch, on_delete=models.CASCADE, related_name="batch_students")
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="batch_memberships")
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["batch", "student"], name="uniq_batch_student")]

    def __str__(self):
        return f"{self.student} in {self.batch.code}"


class LiveClassStatus(models.TextChoices):
    SCHEDULED = "scheduled", "Scheduled"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"


class LiveClass(TimeStampedModel):
    batch = models.ForeignKey(Batch, on_delete=models.CASCADE, related_name="live_classes")
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    meet_url = models.URLField(help_text="Google Meet link (https://meet.google.com/...)")
    start_at = models.DateTimeField(db_index=True)
    duration_minutes = models.PositiveIntegerField(default=90)
    status = models.CharField(max_length=20, choices=LiveClassStatus.choices, default=LiveClassStatus.SCHEDULED)
    recording_lesson = models.ForeignKey(
        Lesson, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
        help_text="Optional: the recorded lesson for this session",
    )

    class Meta:
        ordering = ["start_at"]
        verbose_name_plural = "Live classes"

    def __str__(self):
        return f"{self.title} ({self.start_at:%d %b %Y %H:%M})"

    def clean(self):
        from apps.core.validators import validate_google_meet_url

        if self.meet_url:
            validate_google_meet_url(self.meet_url)

    @property
    def end_at(self):
        return self.start_at + timedelta(minutes=self.duration_minutes)

    @property
    def is_live_now(self):
        now = timezone.now()
        return self.status == LiveClassStatus.SCHEDULED and self.start_at - timedelta(minutes=15) <= now <= self.end_at


class Certificate(models.Model):
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="certificates")
    course = models.ForeignKey(Course, null=True, blank=True, on_delete=models.SET_NULL, related_name="certificates")
    course_title = models.CharField(max_length=200, help_text="Snapshot of course title at issue time")
    certificate_number = models.CharField(max_length=40, unique=True)
    issued_at = models.DateTimeField(default=timezone.now)
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    grade = models.CharField(max_length=40, blank=True)
    is_revoked = models.BooleanField(default=False)
    remarks = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-issued_at"]

    def __str__(self):
        return f"{self.certificate_number} – {self.student}"

    def save(self, *args, **kwargs):
        if not self.course_title and self.course_id:
            self.course_title = self.course.title
        if not self.certificate_number:
            import secrets

            self.certificate_number = f"ATSA-{timezone.now():%Y}-{secrets.token_hex(4).upper()}"
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("core:certificate_verify", args=[self.certificate_number])
