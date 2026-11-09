from django.conf import settings
from django.db import models

from apps.core.models import TimeStampedModel


class Announcement(TimeStampedModel):
    title = models.CharField(max_length=200)
    body = models.TextField()
    course = models.ForeignKey(
        "courses.Course", null=True, blank=True, on_delete=models.CASCADE, related_name="announcements",
        help_text="Leave blank for an academy-wide announcement",
    )
    batch = models.ForeignKey("courses.Batch", null=True, blank=True, on_delete=models.CASCADE, related_name="announcements")
    is_published = models.BooleanField(default=True)
    send_email = models.BooleanField(default=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title


class NotificationKind(models.TextChoices):
    NEW_LESSON = "new_lesson", "New lesson"
    VIDEO_UPDATED = "video_updated", "Video updated"
    LESSON_UPDATED = "lesson_updated", "Lesson updated"
    NEW_MATERIAL = "new_material", "New study material"
    ANNOUNCEMENT = "announcement", "Announcement"
    LIVE_CLASS = "live_class", "Live class"
    ENROLLMENT = "enrollment", "Enrollment"
    PAYMENT = "payment", "Payment"
    ASSIGNMENT = "assignment", "Assignment"
    CERTIFICATE = "certificate", "Certificate"


class Notification(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    kind = models.CharField(max_length=30, choices=NotificationKind.choices)
    title = models.CharField(max_length=200)
    message = models.TextField(blank=True)
    link = models.CharField(max_length=300, blank=True)
    # Same dedupe_key for the same user is never stored twice -> no duplicate notifications
    # when an administrator saves the same update repeatedly.
    dedupe_key = models.CharField(max_length=120, blank=True)
    is_read = models.BooleanField(default=False, db_index=True)
    emailed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "dedupe_key"], condition=~models.Q(dedupe_key=""), name="uniq_notification_dedupe"
            )
        ]

    def __str__(self):
        return f"{self.user}: {self.title}"
