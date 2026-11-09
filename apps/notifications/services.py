import logging

from django.conf import settings
from django.core.mail import get_connection, EmailMessage
from django.db import IntegrityError, transaction

from apps.core.models import SiteSettings

from .models import Notification, NotificationKind

logger = logging.getLogger(__name__)


class Audience:
    SILENT = "silent"
    ALL = "all"
    BATCHES = "batches"

    CHOICES = [
        (SILENT, "Publish silently (no notification)"),
        (ALL, "Notify all enrolled students"),
        (BATCHES, "Notify selected batches only"),
    ]


def notify_users(users, kind, title, message="", link="", dedupe_key="", send_email=True):
    """Create dashboard notifications (and optional emails) for users.

    If dedupe_key is given, users who already received a notification with the
    same key are skipped. Returns the list of users that were newly notified.
    """
    users = list({u.pk: u for u in users}.values())
    if not users:
        return []
    if dedupe_key:
        already = set(
            Notification.objects.filter(user__in=users, dedupe_key=dedupe_key).values_list("user_id", flat=True)
        )
        users = [u for u in users if u.pk not in already]
    created_for = []
    for u in users:
        try:
            with transaction.atomic():
                Notification.objects.create(
                    user=u, kind=kind, title=title[:200], message=message, link=link, dedupe_key=dedupe_key
                )
            created_for.append(u)
        except IntegrityError:  # concurrent save of the same update
            continue

    site = SiteSettings.load()
    if send_email and site.email_notifications_enabled and created_for:
        url = f"{settings.SITE_URL}{link}" if link.startswith("/") else link
        body = f"{message}\n\n{('Open: ' + url) if url else ''}\n\n— {site.site_name}"
        msgs = [
            EmailMessage(subject=f"[{site.site_name}] {title}", body=body, to=[u.email])
            for u in created_for
            if u.email
        ]
        try:
            get_connection().send_messages(msgs)
            Notification.objects.filter(user__in=created_for, dedupe_key=dedupe_key, title=title[:200]).update(emailed=True)
        except Exception:  # never fail the admin action because SMTP is down
            logger.exception("Failed to send notification emails")
    return created_for


def course_audience(course, audience, batches=None):
    """Users with valid access to a course, optionally restricted to batches."""
    from apps.courses.access import active_enrollments  # noqa: avoid cycle
    from apps.enrollments.models import Enrollment, EnrollmentStatus
    from django.contrib.auth import get_user_model
    from django.db.models import Q
    from django.utils import timezone

    if audience == Audience.SILENT:
        return []
    User = get_user_model()
    now = timezone.now()
    valid = Enrollment.objects.filter(
        course=course, status__in=[EnrollmentStatus.ACTIVE, EnrollmentStatus.COMPLETED]
    ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
    if audience == Audience.BATCHES:
        batch_ids = [b.pk for b in (batches or [])]
        if not batch_ids:
            return []
        valid = valid.filter(Q(batch_id__in=batch_ids) | Q(student__batch_memberships__batch_id__in=batch_ids))
    return list(User.objects.filter(pk__in=valid.values("student_id"), is_active=True))


EVENT_TITLES = {
    NotificationKind.NEW_LESSON: "New lesson published",
    NotificationKind.VIDEO_UPDATED: "Lesson video updated",
    NotificationKind.LESSON_UPDATED: "Lesson content updated",
    NotificationKind.NEW_MATERIAL: "New study material added",
}


def notify_lesson_event(lesson, kind, audience, batches=None, send_email=True):
    """Notify students about a lesson change.

    The dedupe key is built from the lesson's content fingerprint, so saving the
    same content again (or re-clicking "notify") never produces duplicates, while
    a genuinely new change produces a new notification.
    """
    if audience == Audience.SILENT or not lesson.is_visible_to_students:
        return []
    course = lesson.module.course
    users = course_audience(course, audience, batches)
    key = f"lesson:{lesson.pk}:{kind}:{lesson.content_fingerprint()}"
    title = f"{EVENT_TITLES.get(kind, 'Lesson update')}: {lesson.title}"
    message = f"Course: {course.title}\nModule: {lesson.module.title}\nLesson: {lesson.title}"
    return notify_users(users, kind, title, message, lesson.get_absolute_url(), key, send_email)
