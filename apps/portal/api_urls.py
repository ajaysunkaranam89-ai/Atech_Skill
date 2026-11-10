"""Small JSON API (Django REST Framework) used by the portal's JavaScript."""
from django.shortcuts import get_object_or_404
from django.urls import path
from django.utils import timezone
from rest_framework import serializers
from rest_framework.decorators import api_view
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from apps.courses.access import can_manage_course, course_progress, lesson_access, valid_enrollment
from apps.courses.models import Course, Lesson, LessonProgress
from apps.notifications.models import Notification


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = ["id", "kind", "title", "message", "link", "is_read", "created_at"]


@api_view(["GET"])
def course_progress_api(request, slug):
    course = get_object_or_404(Course, slug=slug)
    if not (valid_enrollment(request.user, course) or can_manage_course(request.user, course)):
        raise PermissionDenied("Not enrolled")
    done = list(
        LessonProgress.objects.filter(user=request.user, completed=True, lesson__module__course=course).values_list("lesson_id", flat=True)
    )
    return Response({"progress": course_progress(request.user, course), "completed_lessons": done})


@api_view(["POST"])
def lesson_progress_api(request, pk):
    lesson = get_object_or_404(Lesson.objects.select_related("module__course"), pk=pk)
    course = lesson.module.course
    if not lesson_access(request.user, lesson).allowed or not (
        valid_enrollment(request.user, course) or can_manage_course(request.user, course)
    ):
        raise PermissionDenied("No access to this lesson")
    completed = str(request.data.get("completed", True)).lower() not in ("0", "false", "no", "")
    p, _ = LessonProgress.objects.get_or_create(user=request.user, lesson=lesson)
    p.completed = completed
    p.completed_at = timezone.now() if completed else None
    p.save()
    from apps.courses.certificates import maybe_issue_certificate

    cert = maybe_issue_certificate(request.user, course) if completed else None
    return Response({
        "lesson": lesson.pk, "completed": completed, "progress": course_progress(request.user, course),
        "certificate": cert.certificate_number if cert else None,
    })


AUTO_COMPLETE_PERCENT = 90
MAX_JUMP_SECONDS = 30  # skipping ahead does not count as watched


@api_view(["POST"])
def lesson_position_api(request, pk):
    """Save the playback position; mark the lesson complete once ~90% has been watched."""
    lesson = get_object_or_404(Lesson.objects.select_related("module__course"), pk=pk)
    course = lesson.module.course
    if not lesson_access(request.user, lesson).allowed or not (
        valid_enrollment(request.user, course) or can_manage_course(request.user, course)
    ):
        raise PermissionDenied("No access to this lesson")
    try:
        duration = max(int(float(request.data.get("duration", 0))), 0)
        position = min(max(int(float(request.data.get("position", 0))), 0), duration or 10**7)
    except (TypeError, ValueError):
        return Response({"error": "invalid"}, status=400)
    p, _ = LessonProgress.objects.get_or_create(user=request.user, lesson=lesson)
    p.position_seconds = 0 if duration and position >= duration - 5 else position
    if position <= p.furthest_seconds + MAX_JUMP_SECONDS:
        p.furthest_seconds = max(p.furthest_seconds, position)
    if duration:
        p.watched_percent = min(100, int(p.furthest_seconds * 100 / duration))
    newly_completed = False
    if p.watched_percent >= AUTO_COMPLETE_PERCENT and not p.completed:
        p.completed, p.completed_at, newly_completed = True, timezone.now(), True
    p.save()
    cert = None
    if newly_completed:
        from apps.courses.certificates import maybe_issue_certificate

        cert = maybe_issue_certificate(request.user, course)
    return Response({
        "completed": p.completed, "newly_completed": newly_completed, "watched_percent": p.watched_percent,
        "progress": course_progress(request.user, course),
        "certificate_url": f"/learn/certificates/{cert.certificate_number}/" if cert else None,
    })


@api_view(["GET"])
def notifications_api(request):
    qs = Notification.objects.filter(user=request.user)[:20]
    return Response({
        "unread": Notification.objects.filter(user=request.user, is_read=False).count(),
        "results": NotificationSerializer(qs, many=True).data,
    })


@api_view(["POST"])
def notifications_read_all_api(request):
    n = Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
    return Response({"marked": n})


urlpatterns = [
    path("courses/<slug:slug>/progress/", course_progress_api, name="api_course_progress"),
    path("lessons/<int:pk>/progress/", lesson_progress_api, name="api_lesson_progress"),
    path("lessons/<int:pk>/position/", lesson_position_api, name="api_lesson_position"),
    path("notifications/", notifications_api, name="api_notifications"),
    path("notifications/read-all/", notifications_read_all_api, name="api_notifications_read_all"),
]
