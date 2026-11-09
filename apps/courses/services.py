"""Content operations shared by dashboard views and tests."""
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from .models import ContentStatus, Lesson, LessonResource, ResourceType, VideoAsset


def touch(lesson):
    """Mark lesson content as changed so students see it under 'Recently updated'."""
    lesson.content_updated_at = timezone.now()
    lesson.save(update_fields=["content_updated_at", "updated_at"])


@transaction.atomic
def add_video_version(lesson, *, title, source_type, video_url="", video_file=None, thumbnail=None,
                      duration_seconds=0, status=ContentStatus.PUBLISHED, change_note="", user=None,
                      delete_old_file=False):
    """Create a new VideoAsset version and make it the lesson's active video.

    Previous versions are kept (is_active=False, replaced_at set) for audit and
    rollback. Students always get lesson.active_video, so they see the new
    version immediately on their next page load.
    """
    lesson = Lesson.objects.select_for_update().get(pk=lesson.pk)
    previous = lesson.active_video
    next_version = (lesson.videos.aggregate(m=Max("version"))["m"] or 0) + 1
    asset = VideoAsset(
        lesson=lesson,
        title=title or lesson.title,
        source_type=source_type,
        video_url=video_url or "",
        duration_seconds=duration_seconds or 0,
        version=next_version,
        status=status,
        is_active=True,
        change_note=change_note,
        uploaded_by=user,
    )
    if video_file:
        asset.video_file = video_file
    if thumbnail:
        asset.thumbnail = thumbnail
    elif previous and previous.thumbnail:
        asset.thumbnail = previous.thumbnail.name  # keep the existing thumbnail
    asset.full_clean(exclude=["video_file", "thumbnail"])  # files validated by the form
    asset.save()

    now = timezone.now()
    lesson.videos.exclude(pk=asset.pk).filter(is_active=True).update(is_active=False, replaced_at=now)
    if previous and delete_old_file and previous.video_file:
        previous.video_file.delete(save=False)
        VideoAsset.objects.filter(pk=previous.pk).update(video_file="", change_note=(previous.change_note + " [file removed]").strip())

    lesson.active_video = asset
    if asset.duration_seconds and not lesson.duration_minutes:
        lesson.duration_minutes = max(1, round(asset.duration_seconds / 60))
    lesson.content_updated_at = now
    lesson.save(update_fields=["active_video", "duration_minutes", "content_updated_at", "updated_at"])
    return asset, previous


@transaction.atomic
def activate_version(asset):
    """Roll back / forward to a specific stored version."""
    lesson = Lesson.objects.select_for_update().get(pk=asset.lesson_id)
    now = timezone.now()
    lesson.videos.exclude(pk=asset.pk).filter(is_active=True).update(is_active=False, replaced_at=now)
    VideoAsset.objects.filter(pk=asset.pk).update(is_active=True, replaced_at=None)
    lesson.active_video_id = asset.pk
    lesson.content_updated_at = now
    lesson.save(update_fields=["active_video", "content_updated_at", "updated_at"])
    asset.refresh_from_db()
    return asset


def set_lesson_status(lesson, status):
    """Publish/unpublish a lesson together with its active video."""
    was_visible = lesson.is_visible_to_students
    lesson.status = status
    if status == ContentStatus.PUBLISHED:
        lesson.published_at = lesson.published_at or timezone.now()
        lesson.content_updated_at = timezone.now()
    lesson.save()
    v = lesson.active_video
    if v and status in (ContentStatus.PUBLISHED, ContentStatus.UNPUBLISHED) and v.status != status:
        v.status = status
        v.save(update_fields=["status", "updated_at"])
    return was_visible, lesson.is_visible_to_students


def add_pdf_note(lesson, file, title=None):
    return LessonResource.objects.create(
        lesson=lesson, title=title or f"{lesson.title} – notes", resource_type=ResourceType.PDF, file=file,
    )
