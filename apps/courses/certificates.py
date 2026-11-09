"""Automatic certificate issue when a student completes a course."""
from django.utils import timezone

from apps.enrollments.models import Enrollment, EnrollmentStatus
from apps.notifications.models import NotificationKind
from apps.notifications.services import notify_users

from .access import course_progress, valid_enrollment
from .models import Certificate


def maybe_issue_certificate(user, course):
    """Issue a certificate if the student has completed every published lesson.

    Conditions: course has certificates enabled and auto-issue on, student has a
    valid enrollment, progress is 100% of at least one lesson, and the student does
    not already hold a valid certificate for the course. Returns the new Certificate or None.
    """
    if not (course.certificate_enabled and course.auto_issue_certificate):
        return None
    enrollment = valid_enrollment(user, course)
    if enrollment is None:
        return None
    progress = course_progress(user, course)
    if progress["total"] == 0 or progress["completed"] < progress["total"]:
        return None
    if Certificate.objects.filter(student=user, course=course, is_revoked=False).exists():
        return None
    cert = Certificate.objects.create(student=user, course=course, remarks="Issued automatically on course completion")
    Enrollment.objects.filter(pk=enrollment.pk, status=EnrollmentStatus.ACTIVE).update(
        status=EnrollmentStatus.COMPLETED, completed_at=timezone.now()
    )
    notify_users(
        [user], NotificationKind.CERTIFICATE, f"Congratulations! Certificate earned: {course.title}",
        f"You completed every lesson of {course.title}. Your certificate number is {cert.certificate_number}. "
        "Download the PDF from your Certificates page.",
        link=f"/learn/certificates/{cert.certificate_number}/", dedupe_key=f"cert:{cert.pk}",
    )
    return cert
