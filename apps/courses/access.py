"""Single source of truth for who may see/manage what.

Every view that serves course content (pages, video streams, downloads) goes
through these functions, so direct URL manipulation cannot bypass enrollment
or publishing rules.
"""
from dataclasses import dataclass

from django.db.models import Count, Q
from django.utils import timezone

from apps.enrollments.models import Enrollment, EnrollmentStatus

from .models import ContentStatus, Course, Lesson, LessonProgress


def can_manage_course(user, course):
    if not user.is_authenticated or not user.is_active:
        return False
    if user.can_manage_all_content:
        return True
    if user.is_trainer and course is not None:
        return course.trainers.filter(user=user).exists()
    return False


def valid_enrollment(user, course):
    if not user.is_authenticated or course is None:
        return None
    e = Enrollment.objects.filter(student=user, course=course).first()
    return e if e and e.is_valid else None


def active_enrollments(user):
    now = timezone.now()
    return (
        Enrollment.objects.filter(
            student=user,
            status__in=[EnrollmentStatus.ACTIVE, EnrollmentStatus.COMPLETED],
            course__isnull=False,
        )
        .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
        .select_related("course", "batch")
    )


@dataclass
class LessonAccess:
    allowed: bool
    preview_mode: bool = False  # staff viewing possibly-unpublished content
    reason: str = ""


def lesson_access(user, lesson: Lesson) -> LessonAccess:
    course = lesson.module.course
    if user.is_authenticated and can_manage_course(user, course):
        return LessonAccess(True, preview_mode=not lesson.is_visible_to_students)
    if not lesson.is_visible_to_students:
        return LessonAccess(False, reason="This lesson is not available.")
    if lesson.is_free_preview:
        return LessonAccess(True)
    if not user.is_authenticated:
        return LessonAccess(False, reason="Please log in and enroll to watch this lesson.")
    if valid_enrollment(user, course):
        return LessonAccess(True)
    return LessonAccess(False, reason="Enroll in this course to access this lesson.")


def course_progress(user, course):
    total = course.published_lessons().count()
    if not total or not user.is_authenticated:
        return {"total": total, "completed": 0, "percent": 0}
    done = LessonProgress.objects.filter(
        user=user, completed=True, lesson__in=course.published_lessons()
    ).count()
    return {"total": total, "completed": done, "percent": int(round(done * 100 / total))}


def progress_for_courses(user, courses):
    """Batch progress for dashboard cards: {course_id: percent}."""
    ids = [c.id for c in courses]
    totals = dict(
        Course.objects.filter(id__in=ids)
        .annotate(
            n=Count(
                "modules__lessons",
                filter=Q(modules__status=ContentStatus.PUBLISHED, modules__lessons__status=ContentStatus.PUBLISHED),
            )
        )
        .values_list("id", "n")
    )
    done = dict(
        LessonProgress.objects.filter(
            user=user,
            completed=True,
            lesson__status=ContentStatus.PUBLISHED,
            lesson__module__status=ContentStatus.PUBLISHED,
            lesson__module__course_id__in=ids,
        )
        .values_list("lesson__module__course_id")
        .annotate(n=Count("id"))
        .values_list("lesson__module__course_id", "n")
    )
    out = {}
    for cid in ids:
        t = totals.get(cid, 0)
        out[cid] = {"total": t, "completed": done.get(cid, 0), "percent": int(round(done.get(cid, 0) * 100 / t)) if t else 0}
    return out


def student_outline(course):
    """Published modules with their published lessons, in order."""
    modules = list(course.published_modules().order_by("order", "id"))
    lessons = (
        Lesson.objects.filter(module__in=modules, status=ContentStatus.PUBLISHED)
        .select_related("active_video", "module")
        .order_by("module__order", "order", "id")
    )
    by_module = {m.id: [] for m in modules}
    for lesson in lessons:
        by_module[lesson.module_id].append(lesson)
    for m in modules:
        m.visible_lessons = by_module[m.id]
    return modules


def full_outline(course):
    """All modules and lessons (staff preview)."""
    modules = list(course.modules.order_by("order", "id"))
    for m in modules:
        m.visible_lessons = list(m.lessons.select_related("active_video").order_by("order", "id"))
    return modules
