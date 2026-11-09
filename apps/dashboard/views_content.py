"""Recorded Video Management and PDF / Study Material Management."""
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Count
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html
from django.views.decorators.http import require_POST

from apps.core import audit
from apps.courses import services
from apps.courses.access import can_manage_course
from apps.courses.models import (
    ContentStatus,
    Course,
    CourseModule,
    Lesson,
    LessonResource,
    ResourceType,
    VideoAsset,
    VideoSource,
)
from apps.notifications.models import NotificationKind
from apps.notifications.services import notify_lesson_event

from .crud import ManageCreateView, ManageDeleteView, ManageListView, ManageUpdateView, badge
from .decorators import staff_required
from .forms import LessonForm, RecordedVideoCreateForm, ResourceForm, VideoAssetEditForm, VideoReplaceForm


def _check_lesson(request, lesson):
    if not can_manage_course(request.user, lesson.module.course):
        raise PermissionDenied


def _notify(request, form, lesson, kind):
    cd = form.cleaned_data
    audience = cd.get("notify")
    sent = notify_lesson_event(
        lesson, kind, audience, batches=cd.get("notify_batches"), send_email=cd.get("notify_email", True)
    )
    if audience and audience != "silent":
        if not lesson.is_visible_to_students:
            messages.info(request, "No notification sent: the lesson is not visible to students yet (draft/unpublished).")
        elif sent:
            messages.info(request, f"Notified {len(sent)} student(s).")
        else:
            messages.info(request, "No new notifications sent (no eligible students, or they were already notified about this exact change).")
    return sent


def _ajax_or_redirect(request, url):
    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return JsonResponse({"ok": True, "redirect": url})
    return redirect(url)


# --------------------------------------------------------------------- list
class VideoLessonList(ManageListView):
    section = "videos"
    model = Lesson
    title = "Recorded video management"
    search_fields = ["title", "module__title", "module__course__title", "active_video__title"]
    filters = [
        ("module__course_id", "Course", lambda v: [(c.pk, c.title) for c in Course.objects.managed_by(v.request.user)]),
        ("status", "Lesson status", ContentStatus.choices),
        ("active_video__source_type", "Video source", VideoSource.choices),
        ("active_video__isnull", "Has video", [("false", "With video"), ("true", "Without video")]),
    ]
    sort_options = {
        "course": ("Course › module › order", ["module__course__title", "module__order", "order"]),
        "-updated": ("Recently updated", "-content_updated_at"),
        "title": ("Lesson title", "title"),
    }
    default_sort = "course"
    create_url = "dashboard:video_add"
    create_label = "Add recorded video"

    def base_queryset(self):
        return (
            Lesson.objects.filter(module__course__in=Course.objects.managed_by(self.request.user))
            .select_related("module__course", "active_video")
            .annotate(n_versions=Count("videos", distinct=True), n_res=Count("resources", distinct=True))
        )

    def columns(self):
        return ["Lesson", "Course › Module", "Video", "Version", "Order", "Status", "Updated"]

    def row(self, o):
        v = o.active_video
        if v:
            thumb = format_html('<img src="{}" class="thumb-sm me-2" alt="">', v.thumbnail.url) if v.thumbnail else ""
            video = format_html('{}<span class="small">{}</span><br>{}', thumb, v.get_source_type_display(),
                                badge(v.status, "video " + v.get_status_display().lower()))
            version = format_html('v{} <span class="text-muted small">({} total)</span>', v.version, o.n_versions)
        else:
            video, version = format_html('<span class="text-muted">No video</span>'), "—"
        lesson = format_html('<span class="fw-semibold">{}</span>{}', o.title,
                             format_html(' <span class="badge text-bg-info">Free preview</span>') if o.is_free_preview else "")
        return [lesson, f"{o.module.course.title} › {o.module.title}", video, version, o.order,
                badge(o.status, o.get_status_display()), o.content_updated_at.strftime("%d %b %Y %H:%M")]

    def actions(self, o):
        acts = [
            {"label": "Preview", "url": reverse("dashboard:lesson_preview", args=[o.pk]), "icon": "play-circle"},
            {"label": "Edit lesson", "url": reverse("dashboard:lesson_edit", args=[o.pk]), "icon": "pencil"},
            {"label": "Replace video" if o.active_video else "Add video", "url": reverse("dashboard:video_replace", args=[o.pk]), "icon": "arrow-repeat"},
            {"label": "Versions", "url": reverse("dashboard:video_history", args=[o.pk]), "icon": "clock-history"},
            {"label": "Materials", "url": reverse("dashboard:resources") + f"?lesson_id={o.pk}", "icon": "file-earmark-pdf"},
        ]
        if o.status == ContentStatus.PUBLISHED:
            acts.append({"label": "Unpublish", "url": reverse("dashboard:lesson_status", args=[o.pk]), "icon": "eye-slash", "post": {"status": "unpublished"}})
        else:
            acts.append({"label": "Publish", "url": reverse("dashboard:lesson_status", args=[o.pk]), "icon": "send-check", "post": {"status": "published"}})
        acts.append({"label": "Delete", "url": reverse("dashboard:lesson_delete", args=[o.pk]), "icon": "trash", "danger": True})
        return acts


# --------------------------------------------------------------------- add recorded video (lesson + video)
@staff_required("staff")
def video_add(request):
    initial = {}
    if request.GET.get("module"):
        m = CourseModule.objects.filter(pk=request.GET["module"]).first()
        if m:
            initial = {"module": m.pk, "course": m.course_id}
    form = RecordedVideoCreateForm(request.POST or None, request.FILES or None, actor=request.user, initial=initial)
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        if not can_manage_course(request.user, cd["module"].course):
            raise PermissionDenied
        lesson = form.save(commit=False)
        lesson.status = cd["publish"]
        lesson.save()
        if cd.get("source_type"):
            services.add_video_version(
                lesson, title=lesson.title, source_type=cd["source_type"], video_url=cd.get("video_url"),
                video_file=cd.get("video_file"), thumbnail=cd.get("thumbnail"),
                duration_seconds=cd.get("duration_seconds") or 0, status=cd["publish"], user=request.user,
                change_note="Initial upload",
            )
            lesson.refresh_from_db()
        if cd.get("pdf_notes"):
            services.add_pdf_note(lesson, cd["pdf_notes"])
        audit.log(request, "lesson_create", lesson, {"status": lesson.status, "source": cd.get("source_type")})
        messages.success(request, f"Lesson “{lesson.title}” saved as {lesson.get_status_display()}.")
        _notify(request, form, lesson, NotificationKind.NEW_LESSON)
        return _ajax_or_redirect(request, reverse("dashboard:course_builder", args=[lesson.module.course_id]))
    if request.method == "POST" and request.headers.get("x-requested-with") == "XMLHttpRequest":
        resp = render(request, "dashboard/video_form.html", {"form": form, "section": "videos", "title": "Add recorded video"})
        resp.status_code = 400
        return resp
    return render(request, "dashboard/video_form.html", {
        "form": form, "section": "videos", "title": "Add recorded video", "cancel_url": reverse("dashboard:videos"),
    })


# --------------------------------------------------------------------- edit lesson
class LessonScope:
    def get_queryset(self):
        return Lesson.objects.filter(module__course__in=Course.objects.managed_by(self.request.user)).select_related("module__course", "active_video")


class LessonUpdate(LessonScope, ManageUpdateView):
    section = "videos"
    model = Lesson
    form_class = LessonForm
    success_url_name = "dashboard:videos"

    def form_valid(self, form):
        was_visible = Lesson.objects.get(pk=self.object.pk).is_visible_to_students
        content_changed = bool({"title", "description"} & set(form.changed_data))
        if content_changed or "status" in form.changed_data:
            form.instance.content_updated_at = timezone.now()
        response = super().form_valid(form)
        lesson = self.object
        if lesson.active_video and "status" in form.changed_data and lesson.status != ContentStatus.DRAFT:
            services.set_lesson_status(lesson, lesson.status)
        kind = NotificationKind.NEW_LESSON if (not was_visible and lesson.is_visible_to_students) else NotificationKind.LESSON_UPDATED
        _notify(self.request, form, lesson, kind)
        return response

    def get_success_url(self):
        return reverse("dashboard:course_builder", args=[self.object.module.course_id])


class LessonDelete(LessonScope, ManageDeleteView):
    section = "videos"
    model = Lesson
    success_url_name = "dashboard:videos"
    require_typed_confirmation = True
    warning = (
        "Deleting a lesson removes all its video versions, study materials and student progress for this lesson. "
        "Enrollment and payment records are never affected. Tip: Unpublish instead if you may need it again."
    )

    def confirmation_text(self):
        return self.object.title

    def impact(self):
        lesson = self.object
        return [
            ("Video versions", lesson.videos.count()),
            ("Study materials", lesson.resources.count()),
            ("Students with progress", lesson.progress.count()),
        ]

    def form_valid(self, form):
        # Remove uploaded files from storage too (DB rows are cascaded).
        files = [v.video_file for v in self.object.videos.all() if v.video_file] + [
            r.file for r in self.object.resources.all() if r.file
        ]
        response = super().form_valid(form)
        if response.status_code == 302 and not Lesson.objects.filter(pk=self.object.pk).exists():
            for f in files:
                try:
                    f.storage.delete(f.name)
                except Exception:
                    pass
        return response


@staff_required("staff")
@require_POST
def lesson_status(request, pk):
    lesson = get_object_or_404(Lesson.objects.select_related("module__course", "active_video"), pk=pk)
    _check_lesson(request, lesson)
    new = request.POST.get("status")
    if new not in ContentStatus.values:
        return HttpResponseBadRequest("Bad status")
    old = lesson.status
    was_visible, now_visible = services.set_lesson_status(lesson, new)
    audit.log(request, "lesson_status", lesson, {"status": [old, new]})
    messages.success(request, f"Lesson “{lesson.title}” is now {lesson.get_status_display()}.")
    if not was_visible and now_visible and request.POST.get("notify") in ("all",):
        notify_lesson_event(lesson, NotificationKind.NEW_LESSON, "all")
    return redirect(request.POST.get("next") or request.META.get("HTTP_REFERER") or reverse("dashboard:videos"))


# --------------------------------------------------------------------- replace / versions / preview
@staff_required("staff")
def video_replace(request, pk):
    lesson = get_object_or_404(Lesson.objects.select_related("module__course", "active_video"), pk=pk)
    _check_lesson(request, lesson)
    cur = lesson.active_video
    initial = {"title": cur.title if cur else lesson.title, "source_type": cur.source_type if cur else VideoSource.YOUTUBE,
               "video_url": cur.video_url if cur else "", "duration_seconds": cur.duration_seconds if cur else None,
               "status": ContentStatus.PUBLISHED if lesson.status == ContentStatus.PUBLISHED else ContentStatus.DRAFT}
    form = VideoReplaceForm(request.POST or None, request.FILES or None, lesson=lesson, actor=request.user, initial=initial)
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        asset, previous = services.add_video_version(
            lesson, title=cd["title"], source_type=cd["source_type"], video_url=cd.get("video_url"),
            video_file=cd.get("video_file"), thumbnail=cd.get("thumbnail"), duration_seconds=cd.get("duration_seconds") or 0,
            status=cd["status"], change_note=cd.get("change_note", ""), user=request.user, delete_old_file=cd.get("delete_old_file"),
        )
        lesson.refresh_from_db()
        audit.log(request, "video_replace", asset, {
            "lesson": lesson.pk, "from_version": previous.version if previous else None, "to_version": asset.version,
            "source": asset.source_type,
        })
        messages.success(request, f"Video for “{lesson.title}” is now version {asset.version}. Students see it immediately.")
        if asset.status == ContentStatus.PUBLISHED:
            _notify(request, form, lesson, NotificationKind.VIDEO_UPDATED if previous else NotificationKind.NEW_LESSON)
        return _ajax_or_redirect(request, reverse("dashboard:video_history", args=[lesson.pk]))
    ctx = {"form": form, "lesson": lesson, "current": cur, "section": "videos",
           "title": ("Replace video: " if cur else "Add video: ") + lesson.title,
           "cancel_url": reverse("dashboard:video_history", args=[lesson.pk])}
    resp = render(request, "dashboard/video_form.html", ctx)
    if request.method == "POST" and request.headers.get("x-requested-with") == "XMLHttpRequest":
        resp.status_code = 400
    return resp


@staff_required("staff")
def video_history(request, pk):
    lesson = get_object_or_404(Lesson.objects.select_related("module__course", "active_video"), pk=pk)
    _check_lesson(request, lesson)
    versions = lesson.videos.select_related("uploaded_by").order_by("-version")
    return render(request, "dashboard/video_history.html", {"lesson": lesson, "versions": versions, "section": "videos"})


@staff_required("staff")
@require_POST
def video_activate(request, pk):
    asset = get_object_or_404(VideoAsset.objects.select_related("lesson__module__course"), pk=pk)
    _check_lesson(request, asset.lesson)
    if asset.is_file and not asset.video_file:
        messages.error(request, "This version's file was deleted from storage and cannot be restored.")
    else:
        services.activate_version(asset)
        audit.log(request, "video_activate", asset, {"version": asset.version})
        messages.success(request, f"Version {asset.version} is now the active video.")
    return redirect("dashboard:video_history", asset.lesson_id)


class VideoAssetUpdate(ManageUpdateView):
    section = "videos"
    model = VideoAsset
    form_class = VideoAssetEditForm
    success_url_name = "dashboard:videos"

    def get_queryset(self):
        return VideoAsset.objects.filter(lesson__module__course__in=Course.objects.managed_by(self.request.user))

    def get_success_url(self):
        return reverse("dashboard:video_history", args=[self.object.lesson_id])

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["cancel_url"] = self.get_success_url()
        return ctx


class VideoAssetDelete(ManageDeleteView):
    section = "videos"
    model = VideoAsset
    success_url_name = "dashboard:videos"
    warning = "Deletes this stored version (and its uploaded file, if any). The active version cannot be deleted here — replace it first, or delete the lesson."

    def get_queryset(self):
        return VideoAsset.objects.filter(
            lesson__module__course__in=Course.objects.managed_by(self.request.user), is_active=False
        )

    def get_success_url(self):
        return reverse("dashboard:video_history", args=[self.object.lesson_id])

    def form_valid(self, form):
        f = self.object.video_file
        name = f.name if f else None
        response = super().form_valid(form)
        if name:
            try:
                f.storage.delete(name)
            except Exception:
                pass
        return response


@staff_required("staff")
def lesson_preview(request, pk):
    """Admin preview of any lesson (incl. unpublished) or a specific stored version."""
    lesson = get_object_or_404(Lesson.objects.select_related("module__course", "active_video"), pk=pk)
    _check_lesson(request, lesson)
    video = lesson.active_video
    if request.GET.get("version"):
        video = lesson.videos.filter(version=request.GET["version"]).first() or video
    return render(request, "dashboard/lesson_preview.html", {
        "lesson": lesson, "video": video, "section": "videos",
        "resources": lesson.resources.all(),
    })


# --------------------------------------------------------------------- materials
class ResourceScope:
    def get_queryset(self):
        return LessonResource.objects.filter(
            lesson__module__course__in=Course.objects.managed_by(self.request.user)
        ).select_related("lesson__module__course")


class ResourceList(ManageListView):
    section = "materials"
    model = LessonResource
    title = "PDF & study material management"
    search_fields = ["title", "lesson__title", "lesson__module__course__title"]
    filters = [
        ("lesson__module__course_id", "Course", lambda v: [(c.pk, c.title) for c in Course.objects.managed_by(v.request.user)]),
        ("resource_type", "Type", ResourceType.choices),
        ("is_published", "Published", [("true", "Published"), ("false", "Hidden")]),
        ("lesson_id", "Lesson ID", []),
    ]
    sort_options = {"-updated": ("Recently updated", "-updated_at"), "title": ("Title", "title"),
                    "lesson": ("Lesson", ["lesson__module__course__title", "lesson__module__order", "lesson__order", "order"])}
    default_sort = "lesson"
    create_url = "dashboard:resource_add"
    create_label = "Add material"
    edit_url = "dashboard:resource_edit"
    delete_url = "dashboard:resource_delete"

    def base_queryset(self):
        return ResourceScope.get_queryset(self)

    def columns(self):
        return ["Material", "Type", "Lesson", "Downloadable", "Published", "Version", "Updated"]

    def row(self, o):
        return [o.title, o.get_resource_type_display(), f"{o.lesson.module.course.title} › {o.lesson.title}",
                badge(o.is_downloadable), badge(o.is_published), f"v{o.version}", o.updated_at.strftime("%d %b %Y")]


class ResourceCreate(ManageCreateView):
    section = "materials"
    model = LessonResource
    form_class = ResourceForm
    success_url_name = "dashboard:resources"
    upload_progress = True

    def get_initial(self):
        init = super().get_initial()
        if self.request.GET.get("lesson"):
            init["lesson"] = self.request.GET["lesson"]
        return init

    def form_valid(self, form):
        if not can_manage_course(self.request.user, form.cleaned_data["lesson"].module.course):
            raise PermissionDenied
        response = super().form_valid(form)
        r = self.object
        if r.is_published:
            services.touch(r.lesson)
            _notify(self.request, form, r.lesson, NotificationKind.NEW_MATERIAL)
        return response


class ResourceUpdate(ResourceScope, ManageUpdateView):
    section = "materials"
    model = LessonResource
    form_class = ResourceForm
    success_url_name = "dashboard:resources"
    upload_progress = True

    def form_valid(self, form):
        old_file = None
        if "file" in form.changed_data:
            form.instance.version = (form.instance.version or 1) + 1
            old_file = LessonResource.objects.get(pk=form.instance.pk).file
        response = super().form_valid(form)
        if old_file and old_file.name and old_file.name != self.object.file.name:
            try:
                old_file.storage.delete(old_file.name)  # students always get the latest file
            except Exception:
                pass
        if self.object.is_published and set(form.changed_data) - {"notify", "notify_batches", "notify_email", "order"}:
            services.touch(self.object.lesson)
            _notify(self.request, form, self.object.lesson, NotificationKind.NEW_MATERIAL)
        return response


class ResourceDelete(ResourceScope, ManageDeleteView):
    section = "materials"
    model = LessonResource
    success_url_name = "dashboard:resources"

    def form_valid(self, form):
        f = self.object.file
        name = f.name if f else None
        response = super().form_valid(form)
        if name:
            try:
                f.storage.delete(name)
            except Exception:
                pass
        return response
