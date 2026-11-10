"""Student learning portal."""
from datetime import timedelta
from datetime import timezone as dt_timezone

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.core.private_files import serve_private
from apps.courses.certificates import maybe_issue_certificate
from apps.courses.access import (
    active_enrollments,
    can_manage_course,
    course_progress,
    full_outline,
    lesson_access,
    progress_for_courses,
    student_outline,
    valid_enrollment,
)
from apps.courses.models import (
    Assignment,
    AssignmentSubmission,
    Certificate,
    ContentStatus,
    Course,
    Lesson,
    LessonProgress,
    LessonResource,
    LiveClass,
    LiveClassStatus,
    ResourceType,
    SubmissionStatus,
    VideoAsset,
)
from apps.enrollments.models import Payment, UpiPaymentSubmission
from apps.notifications.models import Announcement

from .forms import SubmissionForm


@login_required
def dashboard(request):
    user = request.user
    enrollments = list(active_enrollments(user))
    courses = [e.course for e in enrollments]
    prog = progress_for_courses(user, courses)
    for e in enrollments:
        e.progress = prog[e.course_id]
    course_ids = [c.id for c in courses]
    now = timezone.now()
    visible_lessons = Lesson.objects.visible_to_students().filter(module__course_id__in=course_ids)
    batch_ids = list(user.batch_memberships.values_list("batch_id", flat=True))
    recent = list(visible_lessons.select_related("module__course", "active_video").order_by("-content_updated_at")[:8])
    for l in recent:
        l.is_new = bool(l.published_at and l.content_updated_at - l.published_at < timedelta(hours=1))
    ctx = {
        "enrollments": enrollments,
        "recent_lessons": recent,
        "assignments": Assignment.objects.filter(course_id__in=course_ids, is_published=True)
        .exclude(submissions__student=user).select_related("course").order_by("due_at")[:5],
        "materials": LessonResource.objects.filter(
            is_published=True, lesson__in=visible_lessons
        ).select_related("lesson__module__course").order_by("-updated_at")[:6],
        "certificates": Certificate.objects.filter(student=user, is_revoked=False)[:4],
        "announcements": Announcement.objects.filter(is_published=True).filter(
            Q(course__isnull=True, batch__isnull=True) | Q(course_id__in=course_ids, batch__isnull=True) | Q(batch_id__in=batch_ids)
        )[:4],
        "pending_upi": UpiPaymentSubmission.objects.filter(student=user, status="pending").select_related("course"),
        "now": now,
        "continue_item": _continue_learning(user, enrollments),
        "upcoming": _with_gcal(LiveClass.objects.filter(
            batch_id__in=batch_ids, status=LiveClassStatus.SCHEDULED, start_at__gte=now - timedelta(hours=2)
        ).select_related("batch__course").order_by("start_at")[:8]),
    }
    return render(request, "portal/dashboard.html", ctx)


def _continue_learning(user, enrollments):
    """Most recently viewed course -> its next incomplete published lesson (or first lesson)."""
    valid = {e.course_id: e for e in enrollments}
    if not valid:
        return None
    last = (LessonProgress.objects.filter(user=user, lesson__module__course_id__in=valid)
            .select_related("lesson__module__course").order_by("-last_viewed_at").first())
    enrollment = valid[last.lesson.module.course_id] if last else enrollments[0]
    course = enrollment.course
    done = set(LessonProgress.objects.filter(user=user, completed=True, lesson__module__course=course).values_list("lesson_id", flat=True))
    sequence = [l for m in student_outline(course) for l in m.visible_lessons]
    if not sequence:
        return None
    nxt = next((l for l in sequence if l.pk not in done), None)
    return {"course": course, "lesson": nxt or sequence[-1], "finished": nxt is None,
            "progress": enrollment.progress, "resumed": last is not None}


def _with_gcal(classes):
    items = list(classes)
    for c in items:
        c.gcal = gcal_link(c)
    return items


def gcal_link(lc):
    """Google Calendar 'add event' URL for a live class."""
    from urllib.parse import urlencode

    fmt = "%Y%m%dT%H%M%SZ"
    start = lc.start_at.astimezone(dt_timezone.utc)
    end = lc.end_at.astimezone(dt_timezone.utc)
    return "https://calendar.google.com/calendar/render?" + urlencode({
        "action": "TEMPLATE", "text": f"{lc.title} – {lc.batch.course.title}",
        "dates": f"{start.strftime(fmt)}/{end.strftime(fmt)}", "details": f"Join: {lc.meet_url}", "location": lc.meet_url,
    })


def _course_for_learning(request, slug):
    course = get_object_or_404(Course, slug=slug)
    is_manager = can_manage_course(request.user, course)
    enrollment = valid_enrollment(request.user, course)
    if not (enrollment or is_manager):
        messages.info(request, "Enroll in this course to access the learning content.")
        return None, None, False
    return course, enrollment, is_manager


@login_required
def course_home(request, slug):
    course, enrollment, is_manager = _course_for_learning(request, slug)
    if course is None:
        return redirect("core:course_detail", slug)
    outline = full_outline(course) if is_manager else student_outline(course)
    done = set(LessonProgress.objects.filter(user=request.user, completed=True, lesson__module__course=course).values_list("lesson_id", flat=True))
    first_open = None
    for m in outline:
        for lesson in m.visible_lessons:
            lesson.is_done = lesson.id in done
            if first_open is None and not lesson.is_done:
                first_open = lesson
    return render(request, "portal/course_home.html", {
        "course": course, "outline": outline, "enrollment": enrollment, "progress": course_progress(request.user, course),
        "continue_lesson": first_open, "preview": is_manager,
        "assignments": course.assignments.filter(is_published=True),
    })


def lesson(request, slug, pk):
    lesson = get_object_or_404(Lesson.objects.select_related("module__course", "active_video"), pk=pk, module__course__slug=slug)
    access = lesson_access(request.user, lesson)
    if not access.allowed:
        if not request.user.is_authenticated:
            return redirect(f"{reverse('accounts:login')}?next={request.path}")
        if not lesson.is_visible_to_students:
            raise Http404("Lesson not available")
        messages.warning(request, access.reason)
        return redirect("core:course_detail", slug)

    course = lesson.module.course
    is_manager = request.user.is_authenticated and can_manage_course(request.user, course)
    enrolled = bool(request.user.is_authenticated and valid_enrollment(request.user, course))
    outline = full_outline(course) if is_manager else student_outline(course)
    sequence = [x for m in outline for x in m.visible_lessons]
    idx = next((i for i, x in enumerate(sequence) if x.pk == lesson.pk), None)
    prev_lesson = sequence[idx - 1] if idx else None
    next_lesson = sequence[idx + 1] if idx is not None and idx + 1 < len(sequence) else None

    video = lesson.active_video if access.preview_mode or is_manager else lesson.playable_video
    progress = None
    done = set()
    if request.user.is_authenticated and (enrolled or is_manager):
        progress, _ = LessonProgress.objects.get_or_create(user=request.user, lesson=lesson)
        if video and progress.watched_video_version != video.version:
            progress.watched_video_version = video.version
        progress.save()  # bumps last_viewed_at
        done = set(LessonProgress.objects.filter(user=request.user, completed=True, lesson__module__course=course).values_list("lesson_id", flat=True))
    for x in sequence:
        x.is_done = x.pk in done
    resources = lesson.resources.all() if is_manager else lesson.resources.filter(is_published=True)
    return render(request, "portal/lesson.html", {
        "course": course, "lesson": lesson, "video": video, "outline": outline, "access": access,
        "prev_lesson": prev_lesson, "next_lesson": next_lesson, "progress": progress,
        "resources": resources, "enrolled": enrolled, "is_manager": is_manager,
        "course_progress": course_progress(request.user, course) if request.user.is_authenticated else None,
        "assignments": lesson.assignments.filter(is_published=True),
    })


@login_required
@require_POST
def lesson_complete(request, pk):
    lesson = get_object_or_404(Lesson.objects.select_related("module__course"), pk=pk)
    access = lesson_access(request.user, lesson)
    if not access.allowed or not (valid_enrollment(request.user, lesson.module.course) or can_manage_course(request.user, lesson.module.course)):
        raise PermissionDenied
    completed = request.POST.get("completed", "1") == "1"
    p, _ = LessonProgress.objects.get_or_create(user=request.user, lesson=lesson)
    p.completed = completed
    p.completed_at = timezone.now() if completed else None
    p.save()
    prog = course_progress(request.user, lesson.module.course)
    cert = maybe_issue_certificate(request.user, lesson.module.course) if completed else None
    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return JsonResponse({
            "ok": True, "completed": completed, "progress": prog,
            "certificate": cert.certificate_number if cert else None,
            "certificate_url": reverse("portal:certificate", args=[cert.certificate_number]) if cert else None,
        })
    if cert:
        messages.success(request, f"Congratulations! You completed the course. Certificate {cert.certificate_number} has been issued.")
    nxt = request.POST.get("next")
    if nxt and nxt.startswith("/learn/"):
        return redirect(nxt)
    return redirect(lesson.get_absolute_url())


def video_stream(request, pk):
    """Serve an uploaded MP4 only to users allowed to watch that lesson."""
    asset = get_object_or_404(VideoAsset.objects.select_related("lesson__module__course"), pk=pk)
    lesson = asset.lesson
    access = lesson_access(request.user, lesson)
    if not access.allowed:
        raise PermissionDenied
    is_manager = request.user.is_authenticated and can_manage_course(request.user, lesson.module.course)
    if not is_manager:
        # Students may only stream the current, published version.
        if lesson.active_video_id != asset.pk or asset.status != ContentStatus.PUBLISHED:
            raise PermissionDenied
    if not asset.is_file:
        raise Http404
    return serve_private(request, asset.video_file)


def resource_download(request, pk):
    r = get_object_or_404(LessonResource.objects.select_related("lesson__module__course"), pk=pk)
    access = lesson_access(request.user, r.lesson)
    is_manager = request.user.is_authenticated and can_manage_course(request.user, r.lesson.module.course)
    if not access.allowed or (not is_manager and (not r.is_published or not r.is_downloadable)):
        raise PermissionDenied
    if r.resource_type == ResourceType.LINK:
        return redirect(r.url)
    ext = r.file.name.rsplit(".", 1)[-1] if "." in r.file.name else "bin"
    return serve_private(request, r.file, download=request.GET.get("inline") != "1", filename=f"{r.title}.{ext}")


# ------------------------------------------------------------------ assignments
@login_required
def assignments(request):
    course_ids = [e.course_id for e in active_enrollments(request.user)]
    items = list(Assignment.objects.filter(course_id__in=course_ids, is_published=True).select_related("course", "lesson"))
    subs = {s.assignment_id: s for s in AssignmentSubmission.objects.filter(student=request.user, assignment__in=items)}
    for a in items:
        a.my_submission = subs.get(a.id)
    return render(request, "portal/assignments.html", {"items": items})


@login_required
def assignment_detail(request, pk):
    a = get_object_or_404(Assignment.objects.select_related("course"), pk=pk, is_published=True)
    if not valid_enrollment(request.user, a.course):
        raise PermissionDenied
    sub = AssignmentSubmission.objects.filter(assignment=a, student=request.user).first()
    locked = sub is not None and sub.status == SubmissionStatus.GRADED
    form = SubmissionForm(request.POST or None, request.FILES or None, instance=sub)
    if request.method == "POST" and not locked and form.is_valid():
        s = form.save(commit=False)
        s.assignment = a
        s.student = request.user
        s.status = SubmissionStatus.SUBMITTED
        s.save()
        messages.success(request, "Assignment submitted.")
        return redirect("portal:assignments")
    return render(request, "portal/assignment_detail.html", {"a": a, "sub": sub, "form": form, "locked": locked})


@login_required
def assignment_brief(request, pk):
    a = get_object_or_404(Assignment, pk=pk)
    if not (can_manage_course(request.user, a.course) or (a.is_published and valid_enrollment(request.user, a.course))):
        raise PermissionDenied
    return serve_private(request, a.attachment, download=True)


@login_required
def submission_file(request, pk):
    s = get_object_or_404(AssignmentSubmission.objects.select_related("assignment__course"), pk=pk)
    if not (s.student_id == request.user.pk or can_manage_course(request.user, s.assignment.course)):
        raise PermissionDenied
    return serve_private(request, s.file, download=True)


@login_required
def upi_screenshot(request, pk):
    s = get_object_or_404(UpiPaymentSubmission, pk=pk)
    if not (s.student_id == request.user.pk or request.user.is_super_admin):
        raise PermissionDenied
    return serve_private(request, s.screenshot)


# ------------------------------------------------------------------ certificates & payments
@login_required
def certificates(request):
    return render(request, "portal/certificates.html", {"items": Certificate.objects.filter(student=request.user)})


@login_required
def certificate(request, number):
    c = get_object_or_404(Certificate.objects.select_related("student", "issued_by"), certificate_number=number)
    if not (c.student_id == request.user.pk or request.user.can_manage_all_content):
        raise PermissionDenied
    return render(request, "portal/certificate_print.html", {"c": c})


@login_required
def payments(request):
    return render(request, "portal/payments.html", {
        "payments": Payment.objects.filter(student=request.user).select_related("invoice").order_by("-created_at"),
        "upi": UpiPaymentSubmission.objects.filter(student=request.user).select_related("course"),
    })


# ------------------------------------------------------------------ PDFs
def _pdf_response(data, filename):
    from django.http import HttpResponse

    resp = HttpResponse(data, content_type="application/pdf")
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    resp["Cache-Control"] = "private, no-store"
    return resp


@login_required
def certificate_pdf(request, number):
    from django.conf import settings as dj_settings

    from apps.core.models import SiteSettings
    from apps.core.pdf import certificate_pdf as render

    c = get_object_or_404(Certificate.objects.select_related("student"), certificate_number=number)
    if not (c.student_id == request.user.pk or request.user.can_manage_all_content):
        raise PermissionDenied
    if c.is_revoked and not request.user.can_manage_all_content:
        raise Http404
    site = SiteSettings.load()
    verify = dj_settings.SITE_URL + reverse("core:certificate_verify", args=[c.certificate_number])
    return _pdf_response(render(c, verify, site.site_name, site.tagline), f"Certificate-{c.certificate_number}.pdf")


@login_required
def invoice_pdf(request, payment_id):
    from apps.core.pdf import invoice_pdf as render
    from apps.enrollments.invoicing import amount_in_words, issue_invoice
    from apps.enrollments.models import PaymentStatus

    payment = get_object_or_404(Payment.objects.select_related("student"), pk=payment_id)
    if not (payment.student_id == request.user.pk or request.user.is_super_admin):
        raise PermissionDenied
    if payment.status != PaymentStatus.PAID:
        raise Http404("No invoice for unpaid payments")
    inv = issue_invoice(payment)  # returns existing invoice, or creates one for older payments
    return _pdf_response(render(inv, amount_in_words(inv.total)), f"{inv.number.replace('/', '-')}.pdf")
