"""Overview, batches, live classes, assignments, certificates, notifications, reports, settings, audit."""
import csv
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db.models import Avg, Count, Q, Sum
from django.db.models.functions import TruncMonth
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html
from django.views.decorators.http import require_POST

from apps.accounts.models import Role
from apps.core import audit
from apps.core.models import AuditLog, SiteSettings
from apps.courses.access import can_manage_course
from apps.courses.models import (
    Assignment,
    AssignmentSubmission,
    Batch,
    BatchStatus,
    BatchStudent,
    Certificate,
    Course,
    CourseStatus,
    Lesson,
    LessonProgress,
    LiveClass,
    LiveClassStatus,
    SubmissionStatus,
)
from apps.enrollments.models import Enrollment, EnrollmentStatus, Payment, PaymentStatus, UpiPaymentSubmission, UpiStatus
from apps.notifications.models import Announcement, Notification, NotificationKind
from apps.notifications.services import course_audience, notify_users

from .crud import ManageCreateView, ManageDeleteView, ManageListView, ManageUpdateView, badge, has_cap
from .decorators import staff_required
from .forms import (
    AnnouncementForm,
    AssignmentForm,
    BatchAddStudentForm,
    BatchForm,
    CertificateForm,
    GradeForm,
    LiveClassForm,
    SiteSettingsForm,
)

User = get_user_model()


def managed(user):
    return Course.objects.managed_by(user)


# --------------------------------------------------------------------- overview
@staff_required("staff")
def overview(request):
    u = request.user
    courses = managed(u)
    now = timezone.now()
    ctx = {
        "section": "overview",
        "stats": {
            "courses": courses.count(),
            "published": courses.filter(status=CourseStatus.PUBLISHED).count(),
            "lessons": Lesson.objects.filter(module__course__in=courses).count(),
            "students": Enrollment.objects.filter(course__in=courses, status=EnrollmentStatus.ACTIVE).values("student").distinct().count(),
        },
        "upcoming_classes": LiveClass.objects.filter(batch__course__in=courses, start_at__gte=now - timedelta(hours=2),
                                                     status=LiveClassStatus.SCHEDULED).select_related("batch__course")[:6],
        "recent_lessons": Lesson.objects.filter(module__course__in=courses).select_related("module__course").order_by("-content_updated_at")[:8],
        "pending_submissions": AssignmentSubmission.objects.filter(assignment__course__in=courses, status=SubmissionStatus.SUBMITTED).count(),
    }
    if u.can_manage_all_content:
        from apps.core.models import Enquiry, EnquiryStatus

        ctx["leads"] = {
            "new": Enquiry.objects.filter(status=EnquiryStatus.NEW).count(),
            "week": Enquiry.objects.filter(created_at__gte=now - timedelta(days=7)).count(),
            "due": Enquiry.objects.filter(follow_up_on__lte=timezone.localdate()).exclude(
                status__in=[EnquiryStatus.CONVERTED, EnquiryStatus.CLOSED]).count(),
        }
    if u.is_super_admin:
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        paid = Payment.objects.filter(status=PaymentStatus.PAID)
        ctx["finance"] = {
            "revenue_month": paid.filter(paid_at__gte=month_start).aggregate(s=Sum("amount"))["s"] or 0,
            "revenue_total": paid.aggregate(s=Sum("amount"))["s"] or 0,
            "pending_upi": UpiPaymentSubmission.objects.filter(status=UpiStatus.PENDING).count(),
            "new_students_30d": User.objects.filter(role=Role.STUDENT, date_joined__gte=now - timedelta(days=30)).count(),
        }
        ctx["recent_payments"] = paid.select_related("student").order_by("-paid_at")[:6]
        from apps.core import ops as _ops

        ctx["backup"] = _ops.backup_status()
        ctx["email"] = _ops.email_status()
    return render(request, "dashboard/overview.html", ctx)


# --------------------------------------------------------------------- batches
class BatchScope:
    def get_queryset(self):
        return Batch.objects.filter(course__in=managed(self.request.user)).select_related("course", "trainer__user")


class BatchList(ManageListView):
    section = "batches"
    model = Batch
    title = "Batch management"
    search_fields = ["name", "code", "course__title"]
    filters = [("course_id", "Course", lambda v: [(c.pk, c.title) for c in managed(v.request.user)]),
               ("status", "Status", BatchStatus.choices)]
    sort_options = {"-start": ("Start date ↓", "-start_date"), "start": ("Start date ↑", "start_date"), "code": ("Code", "code")}
    default_sort = "-start"
    create_url = "dashboard:batch_add"
    create_label = "Add batch"
    edit_url = "dashboard:batch_edit"
    delete_url = "dashboard:batch_delete"

    def base_queryset(self):
        return BatchScope.get_queryset(self).annotate(n=Count("batch_students"))

    def columns(self):
        return ["Batch", "Course", "Trainer", "Dates", "Schedule", "Seats", "Status"]

    def row(self, o):
        return [format_html('<a class="fw-semibold" href="{}">{}</a><div class="small text-muted">{}</div>',
                            reverse("dashboard:batch_detail", args=[o.pk]), o.code, o.name),
                o.course.title, o.trainer or "—",
                f"{o.start_date:%d %b %Y} – {o.end_date:%d %b %Y}" if o.end_date else f"From {o.start_date:%d %b %Y}",
                o.schedule or "—", f"{o.n}/{o.capacity}", badge(o.status, o.get_status_display())]

    def can_delete(self, obj):
        return has_cap(self.request.user, "content")


class BatchCreate(ManageCreateView):
    capability = "content"
    section = "batches"
    model = Batch
    form_class = BatchForm
    success_url_name = "dashboard:batches"


class BatchUpdate(BatchScope, ManageUpdateView):
    capability = "content"
    section = "batches"
    model = Batch
    form_class = BatchForm
    success_url_name = "dashboard:batches"


class BatchDelete(BatchScope, ManageDeleteView):
    capability = "content"
    section = "batches"
    model = Batch
    success_url_name = "dashboard:batches"
    require_typed_confirmation = True
    warning = "Deletes the batch and its live-class schedule. Enrollments stay active (the batch link is cleared)."

    def confirmation_text(self):
        return self.object.code


@staff_required("staff")
def batch_detail(request, pk):
    batch = get_object_or_404(Batch.objects.select_related("course"), pk=pk)
    if not can_manage_course(request.user, batch.course):
        raise PermissionDenied
    form = BatchAddStudentForm(request.POST or None)
    form.fields["student"].queryset = User.objects.filter(
        role=Role.STUDENT, enrollments__course=batch.course, enrollments__status=EnrollmentStatus.ACTIVE
    ).exclude(batch_memberships__batch=batch).distinct()
    if request.method == "POST" and has_cap(request.user, "content") and form.is_valid():
        s = form.cleaned_data["student"]
        if not batch.add_student(s, force=request.POST.get("force") == "1"):
            messages.error(request, f"{batch.code} is full ({batch.capacity} seats). Increase the capacity, or tick "
                                    "'Add even if full' to exceed it.")
            return redirect("dashboard:batch_detail", pk)
        Enrollment.objects.filter(student=s, course=batch.course).update(batch=batch)
        audit.log(request, "batch_add_student", batch, {"student": s.email})
        messages.success(request, f"{s} added to {batch.code}.")
        return redirect("dashboard:batch_detail", pk)
    return render(request, "dashboard/batch_detail.html", {
        "batch": batch, "form": form, "section": "batches",
        "members": batch.batch_students.select_related("student").order_by("student__first_name"),
        "classes": batch.live_classes.order_by("start_at"),
    })


@staff_required("content")
@require_POST
def batch_remove_student(request, pk, user_id):
    batch = get_object_or_404(Batch, pk=pk)
    BatchStudent.objects.filter(batch=batch, student_id=user_id).delete()
    Enrollment.objects.filter(student_id=user_id, course=batch.course, batch=batch).update(batch=None)
    audit.log(request, "batch_remove_student", batch, {"student_id": user_id})
    messages.success(request, "Student removed from batch (enrollment kept).")
    return redirect("dashboard:batch_detail", pk)


# --------------------------------------------------------------------- live classes
class LiveScope:
    def get_queryset(self):
        return LiveClass.objects.filter(batch__course__in=managed(self.request.user)).select_related("batch__course")


def _notify_live(request, lc, created):
    users = list(User.objects.filter(batch_memberships__batch=lc.batch, is_active=True))
    key = f"live:{lc.pk}:{lc.start_at:%Y%m%d%H%M}:{lc.status}:{lc.meet_url}"
    title = ("Live class scheduled: " if created else "Live class updated: ") + lc.title
    if lc.status == LiveClassStatus.CANCELLED:
        title = "Live class cancelled: " + lc.title
    local = timezone.localtime(lc.start_at)
    msg = f"{lc.batch.course.title} – {lc.batch.code}\n{local:%A %d %b %Y, %I:%M %p} ({lc.duration_minutes} min)"
    sent = notify_users(users, NotificationKind.LIVE_CLASS, title, msg, "/learn/", key)
    messages.info(request, f"Notified {len(sent)} batch student(s).")


class LiveClassList(ManageListView):
    section = "live"
    model = LiveClass
    title = "Live class management (Google Meet)"
    search_fields = ["title", "batch__code", "batch__course__title"]
    filters = [("batch_id", "Batch", lambda v: [(b.pk, str(b)) for b in Batch.objects.filter(course__in=managed(v.request.user))]),
               ("status", "Status", LiveClassStatus.choices)]
    sort_options = {"start": ("Upcoming first", "start_at"), "-start": ("Latest first", "-start_at")}
    default_sort = "start"
    create_url = "dashboard:live_add"
    create_label = "Schedule live class"
    edit_url = "dashboard:live_edit"
    delete_url = "dashboard:live_delete"

    def base_queryset(self):
        return LiveScope.get_queryset(self)

    def get_queryset(self):
        qs = super().get_queryset()
        if not self.request.GET.get("past"):
            qs = qs.filter(start_at__gte=timezone.now() - timedelta(hours=3))
        return qs

    def columns(self):
        return ["When", "Class", "Batch", "Duration", "Meet link", "Status"]

    def row(self, o):
        return [timezone.localtime(o.start_at).strftime("%a %d %b %Y, %I:%M %p"), o.title, f"{o.batch.code} ({o.batch.course.title})",
                f"{o.duration_minutes} min", format_html('<a href="{}" target="_blank" rel="noopener">Open Meet</a>', o.meet_url),
                badge(o.status, o.get_status_display())]

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["extra_buttons"] = [("Show past classes", reverse("dashboard:live") + "?past=1", "clock-history")]
        return ctx


class LiveClassCreate(ManageCreateView):
    section = "live"
    model = LiveClass
    form_class = LiveClassForm
    success_url_name = "dashboard:live"

    def form_valid(self, form):
        if not can_manage_course(self.request.user, form.cleaned_data["batch"].course):
            raise PermissionDenied
        response = super().form_valid(form)
        if form.cleaned_data.get("notify_batch"):
            _notify_live(self.request, self.object, True)
        return response


class LiveClassUpdate(LiveScope, ManageUpdateView):
    section = "live"
    model = LiveClass
    form_class = LiveClassForm
    success_url_name = "dashboard:live"

    def form_valid(self, form):
        response = super().form_valid(form)
        if form.cleaned_data.get("notify_batch") and set(form.changed_data) & {"start_at", "meet_url", "status", "title"}:
            _notify_live(self.request, self.object, False)
        return response


class LiveClassDelete(LiveScope, ManageDeleteView):
    section = "live"
    model = LiveClass
    success_url_name = "dashboard:live"


# --------------------------------------------------------------------- assignments
class AssignmentScope:
    def get_queryset(self):
        return Assignment.objects.filter(course__in=managed(self.request.user)).select_related("course", "lesson")


class AssignmentList(ManageListView):
    section = "assignments"
    model = Assignment
    title = "Assignment management"
    search_fields = ["title", "course__title"]
    filters = [("course_id", "Course", lambda v: [(c.pk, c.title) for c in managed(v.request.user)]),
               ("is_published", "Published", [("true", "Published"), ("false", "Draft")])]
    sort_options = {"due": ("Due date", "due_at"), "-created": ("Newest", "-created_at")}
    default_sort = "-created"
    create_url = "dashboard:assignment_add"
    create_label = "Add assignment"
    edit_url = "dashboard:assignment_edit"
    delete_url = "dashboard:assignment_delete"

    def base_queryset(self):
        return AssignmentScope.get_queryset(self).annotate(
            n=Count("submissions"), pending=Count("submissions", filter=Q(submissions__status=SubmissionStatus.SUBMITTED))
        )

    def columns(self):
        return ["Assignment", "Course", "Due", "Submissions", "To grade", "Published"]

    def row(self, o):
        return [o.title, o.course.title, timezone.localtime(o.due_at).strftime("%d %b %Y %H:%M") if o.due_at else "—",
                o.n, format_html('<span class="badge text-bg-warning">{}</span>', o.pending) if o.pending else 0, badge(o.is_published)]

    def actions(self, o):
        return [{"label": "Submissions", "url": reverse("dashboard:submissions", args=[o.pk]), "icon": "inbox"}] + super().actions(o)


class AssignmentCreate(ManageCreateView):
    section = "assignments"
    model = Assignment
    form_class = AssignmentForm
    success_url_name = "dashboard:assignments"

    def form_valid(self, form):
        response = super().form_valid(form)
        a = self.object
        if a.is_published:
            notify_users(course_audience(a.course, "all"), NotificationKind.ASSIGNMENT, f"New assignment: {a.title}",
                         f"Course: {a.course.title}", "/learn/assignments/", f"assignment:{a.pk}:published")
        return response


class AssignmentUpdate(AssignmentScope, ManageUpdateView):
    section = "assignments"
    model = Assignment
    form_class = AssignmentForm
    success_url_name = "dashboard:assignments"

    def form_valid(self, form):
        response = super().form_valid(form)
        a = self.object
        if a.is_published:
            notify_users(course_audience(a.course, "all"), NotificationKind.ASSIGNMENT, f"New assignment: {a.title}",
                         f"Course: {a.course.title}", "/learn/assignments/", f"assignment:{a.pk}:published")
        return response


class AssignmentDelete(AssignmentScope, ManageDeleteView):
    section = "assignments"
    model = Assignment
    success_url_name = "dashboard:assignments"
    warning = "All student submissions for this assignment will also be deleted."


@staff_required("staff")
def submissions(request, pk):
    a = get_object_or_404(Assignment.objects.select_related("course"), pk=pk)
    if not can_manage_course(request.user, a.course):
        raise PermissionDenied
    subs = a.submissions.select_related("student", "graded_by")
    return render(request, "dashboard/submissions.html", {"assignment": a, "subs": subs, "section": "assignments"})


@staff_required("staff")
def grade_submission(request, pk):
    sub = get_object_or_404(AssignmentSubmission.objects.select_related("assignment__course", "student"), pk=pk)
    if not can_manage_course(request.user, sub.assignment.course):
        raise PermissionDenied
    form = GradeForm(request.POST or None, instance=sub)
    if request.method == "POST" and form.is_valid():
        s = form.save(commit=False)
        s.graded_by = request.user
        s.graded_at = timezone.now()
        s.save()
        audit.log(request, "grade", s, audit.diff_form(form))
        notify_users([s.student], NotificationKind.ASSIGNMENT, f"Assignment reviewed: {sub.assignment.title}",
                     f"Status: {s.get_status_display()}" + (f" — Marks {s.marks}/{sub.assignment.max_marks}" if s.marks is not None else ""),
                     "/learn/assignments/", f"grade:{s.pk}:{s.graded_at:%Y%m%d%H%M%S}")
        messages.success(request, "Submission graded.")
        return redirect("dashboard:submissions", sub.assignment_id)
    return render(request, "dashboard/grade.html", {"sub": sub, "form": form, "section": "assignments"})


# --------------------------------------------------------------------- certificates
class CertificateList(ManageListView):
    capability = "content"
    section = "certificates"
    model = Certificate
    title = "Certificate management"
    search_fields = ["certificate_number", "student__email", "student__first_name", "course_title"]
    filters = [("is_revoked", "State", [("false", "Valid"), ("true", "Revoked")])]
    sort_options = {"-issued": ("Newest", "-issued_at")}
    default_sort = "-issued"
    create_url = "dashboard:certificate_add"
    create_label = "Issue certificate"

    def base_queryset(self):
        return Certificate.objects.select_related("student")

    def columns(self):
        return ["Number", "Student", "Course", "Issued", "Grade", "State"]

    def row(self, o):
        return [o.certificate_number, o.student.get_full_name() or o.student.email, o.course_title,
                o.issued_at.strftime("%d %b %Y"), o.grade or "—", badge(not o.is_revoked, "Revoked" if o.is_revoked else "Valid")]

    def actions(self, o):
        acts = [{"label": "View", "url": reverse("portal:certificate", args=[o.certificate_number]), "icon": "award"},
                {"label": "PDF", "url": reverse("portal:certificate_pdf", args=[o.certificate_number]), "icon": "file-earmark-pdf"}]
        acts.append({"label": "Restore" if o.is_revoked else "Revoke", "url": reverse("dashboard:certificate_revoke", args=[o.pk]),
                     "icon": "slash-circle", "post": {"x": "1"}, "danger": not o.is_revoked})
        return acts


class CertificateCreate(ManageCreateView):
    capability = "content"
    section = "certificates"
    model = Certificate
    form_class = CertificateForm
    success_url_name = "dashboard:certificates"

    def form_valid(self, form):
        form.instance.issued_by = self.request.user
        response = super().form_valid(form)
        c = self.object
        notify_users([c.student], NotificationKind.CERTIFICATE, f"Certificate issued: {c.course_title}",
                     f"Certificate number {c.certificate_number}", f"/learn/certificates/{c.certificate_number}/", f"cert:{c.pk}")
        Enrollment.objects.filter(student=c.student, course=c.course, status=EnrollmentStatus.ACTIVE).update(
            status=EnrollmentStatus.COMPLETED, completed_at=timezone.now())
        return response


@staff_required("content")
@require_POST
def certificate_revoke(request, pk):
    c = get_object_or_404(Certificate, pk=pk)
    c.is_revoked = not c.is_revoked
    c.save(update_fields=["is_revoked"])
    audit.log(request, "certificate_revoke" if c.is_revoked else "certificate_restore", c)
    messages.success(request, f"Certificate {c.certificate_number} {'revoked' if c.is_revoked else 'restored'}.")
    return redirect("dashboard:certificates")


# --------------------------------------------------------------------- announcements / notifications
class AnnouncementScope:
    def get_queryset(self):
        qs = Announcement.objects.select_related("course", "batch")
        if self.request.user.can_manage_all_content:
            return qs
        return qs.filter(course__in=managed(self.request.user))


def _send_announcement(request, a):
    if not a.is_published:
        return
    if a.batch_id:
        users = list(User.objects.filter(batch_memberships__batch=a.batch, is_active=True))
    elif a.course_id:
        users = course_audience(a.course, "all")
    else:
        users = list(User.objects.filter(role=Role.STUDENT, is_active=True))
    sent = notify_users(users, NotificationKind.ANNOUNCEMENT, a.title, a.body, "/learn/", f"announcement:{a.pk}", send_email=a.send_email)
    messages.info(request, f"Announcement delivered to {len(sent)} student(s).")


class AnnouncementList(ManageListView):
    section = "notifications"
    model = Announcement
    title = "Notifications & announcements"
    search_fields = ["title", "body"]
    sort_options = {"-created": ("Newest", "-created_at")}
    default_sort = "-created"
    create_url = "dashboard:announcement_add"
    create_label = "New announcement"
    edit_url = "dashboard:announcement_edit"
    delete_url = "dashboard:announcement_delete"

    def base_queryset(self):
        return AnnouncementScope.get_queryset(self)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["extra_buttons"] = [("Delivery log", reverse("dashboard:notification_log"), "list-check")]
        return ctx

    def columns(self):
        return ["Title", "Audience", "Email", "Published", "Created"]

    def row(self, o):
        aud = f"Batch {o.batch.code}" if o.batch else (o.course.title if o.course else "All students")
        return [o.title, aud, badge(o.send_email), badge(o.is_published), o.created_at.strftime("%d %b %Y %H:%M")]


class AnnouncementCreate(ManageCreateView):
    section = "notifications"
    model = Announcement
    form_class = AnnouncementForm
    success_url_name = "dashboard:announcements"

    def form_valid(self, form):
        response = super().form_valid(form)
        _send_announcement(self.request, self.object)
        return response


class AnnouncementUpdate(AnnouncementScope, ManageUpdateView):
    section = "notifications"
    model = Announcement
    form_class = AnnouncementForm
    success_url_name = "dashboard:announcements"

    def form_valid(self, form):
        response = super().form_valid(form)
        _send_announcement(self.request, self.object)  # dedupe key prevents re-sending
        return response


class AnnouncementDelete(AnnouncementScope, ManageDeleteView):
    section = "notifications"
    model = Announcement
    success_url_name = "dashboard:announcements"


class NotificationLog(ManageListView):
    capability = "content"
    section = "notifications"
    model = Notification
    title = "Notification delivery log"
    search_fields = ["title", "user__email"]
    filters = [("kind", "Type", NotificationKind.choices), ("is_read", "Read", [("true", "Read"), ("false", "Unread")])]
    sort_options = {"-created": ("Newest", "-created_at")}
    default_sort = "-created"
    paginate_by = 50

    def base_queryset(self):
        return Notification.objects.select_related("user")

    def columns(self):
        return ["Sent", "Student", "Type", "Title", "Email", "Read"]

    def row(self, o):
        return [o.created_at.strftime("%d %b %Y %H:%M"), o.user.email, o.get_kind_display(), o.title, badge(o.emailed), badge(o.is_read)]

    def actions(self, o):
        return []


# --------------------------------------------------------------------- reports
@staff_required("super")
def reports(request):
    paid = Payment.objects.filter(status=PaymentStatus.PAID)
    since = timezone.now() - timedelta(days=365)
    revenue_by_month = (
        paid.filter(paid_at__gte=since).annotate(m=TruncMonth("paid_at")).values("m")
        .annotate(total=Sum("amount"), n=Count("id")).order_by("m")
    )
    courses = Course.objects.annotate(
        active=Count("enrollments", filter=Q(enrollments__status=EnrollmentStatus.ACTIVE), distinct=True),
        completed=Count("enrollments", filter=Q(enrollments__status=EnrollmentStatus.COMPLETED), distinct=True),
        revenue=Sum("payments__amount", filter=Q(payments__status=PaymentStatus.PAID)),
    ).order_by("-active")
    top_lessons = (
        LessonProgress.objects.filter(completed=True).values("lesson__title", "lesson__module__course__title")
        .annotate(n=Count("id")).order_by("-n")[:10]
    )
    if request.GET.get("export") == "payments":
        resp = HttpResponse(content_type="text/csv")
        resp["Content-Disposition"] = 'attachment; filename="payments.csv"'
        w = csv.writer(resp)
        w.writerow(["date", "student", "course", "gateway", "amount", "status", "reference"])
        for p in Payment.objects.select_related("student").order_by("-created_at"):
            w.writerow([p.created_at.isoformat(), p.student.email, p.course_title, p.gateway, p.amount, p.status,
                        p.razorpay_payment_id or p.upi_reference])
        audit.log(request, "export", object_type="payments")
        return resp
    if request.GET.get("export") == "enrollments":
        resp = HttpResponse(content_type="text/csv")
        resp["Content-Disposition"] = 'attachment; filename="enrollments.csv"'
        w = csv.writer(resp)
        w.writerow(["student", "name", "phone", "course", "batch", "status", "source", "activated", "expires"])
        for e in Enrollment.objects.select_related("student", "batch").order_by("-created_at"):
            w.writerow([e.student.email, e.student.get_full_name(), e.student.phone, e.course_title, e.batch.code if e.batch else "",
                        e.status, e.source, e.activated_at.isoformat() if e.activated_at else "", e.expires_at.isoformat() if e.expires_at else ""])
        audit.log(request, "export", object_type="enrollments")
        return resp
    max_rev = max([r["total"] for r in revenue_by_month] or [1])
    return render(request, "dashboard/reports.html", {
        "section": "reports", "revenue_by_month": revenue_by_month, "max_rev": max_rev, "courses": courses,
        "top_lessons": top_lessons,
        "totals": paid.aggregate(total=Sum("amount"), n=Count("id"), avg=Avg("amount")),
        "students": User.objects.filter(role=Role.STUDENT).count(),
    })


# --------------------------------------------------------------------- settings & audit
@staff_required("super")
def site_settings(request):
    obj = SiteSettings.load()
    obj = SiteSettings.objects.get(pk=obj.pk)
    form = SiteSettingsForm(request.POST or None, request.FILES or None, instance=obj, actor=request.user)
    if request.method == "POST" and form.is_valid():
        changes = audit.diff_form(form)
        form.save()
        audit.log(request, "settings_update", form.instance, changes)
        messages.success(request, "Website settings saved. Changes are live.")
        return redirect("dashboard:settings")
    return render(request, "dashboard/form.html", {"form": form, "title": "Website settings", "section": "settings",
                                                   "cancel_url": reverse("dashboard:overview")})


class AuditLogList(ManageListView):
    capability = "super"
    section = "audit"
    model = AuditLog
    title = "Audit log"
    search_fields = ["action", "object_type", "object_repr", "actor__email"]
    filters = [("object_type", "Object", lambda v: [(t, t) for t in AuditLog.objects.values_list("object_type", flat=True).distinct().order_by("object_type")])]
    sort_options = {"-created": ("Newest", "-created_at")}
    default_sort = "-created"
    paginate_by = 50

    def base_queryset(self):
        return AuditLog.objects.select_related("actor")

    def columns(self):
        return ["When", "Who", "Action", "Object", "Changes", "IP"]

    def row(self, o):
        changes = ", ".join(sorted(o.changes.keys()))[:120] if isinstance(o.changes, dict) else ""
        return [timezone.localtime(o.created_at).strftime("%d %b %Y %H:%M:%S"), o.actor.email if o.actor else "system",
                o.action, f"{o.object_type} #{o.object_id} {o.object_repr}", changes, o.ip_address or ""]

    def actions(self, o):
        return []


# --------------------------------------------------------------------- enquiries (leads)
from apps.core.models import Enquiry, EnquiryStatus  # noqa: E402

from .forms import EnquiryUpdateForm  # noqa: E402


class EnquiryList(ManageListView):
    capability = "content"
    section = "enquiries"
    model = Enquiry
    title = "Enquiries (leads)"
    search_fields = ["name", "phone", "email", "message", "notes", "utm_campaign"]
    filters = [
        ("status", "Status", EnquiryStatus.choices),
        ("course_id", "Course", lambda v: [(c.pk, c.title) for c in Course.objects.all()]),
        ("kind", "Interest", Enquiry.KIND_CHOICES),
        ("utm_source", "Campaign source",
         lambda v: [(s, s) for s in Enquiry.objects.exclude(utm_source="").values_list("utm_source", flat=True).distinct()]),
    ]
    sort_options = {"-created": ("Newest", "-created_at"), "follow": ("Follow-up date", "follow_up_on"), "status": ("Status", "status")}
    default_sort = "-created"
    paginate_by = 30

    def base_queryset(self):
        return Enquiry.objects.select_related("course", "assigned_to")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["extra_buttons"] = [("Export CSV", reverse("dashboard:enquiry_export") + "?" + self.request.GET.urlencode(), "download")]
        new = Enquiry.objects.filter(status=EnquiryStatus.NEW).count()
        week = Enquiry.objects.filter(created_at__gte=timezone.now() - timedelta(days=7)).count()
        conv = Enquiry.objects.filter(status=EnquiryStatus.CONVERTED).count()
        total = Enquiry.objects.count()
        ctx["summary_html"] = format_html(
            "<strong>{}</strong> new · <strong>{}</strong> in the last 7 days · <strong>{}</strong> converted of {} ({}%)",
            new, week, conv, total, round(conv * 100 / total) if total else 0,
        )
        return ctx

    def columns(self):
        return ["Received", "Lead", "Course / interest", "Source", "Status", "Follow-up", "Owner"]

    def row(self, o):
        lead = format_html('<a class="fw-semibold" href="{}">{}</a><div class="small">{}{}</div>',
                           reverse("dashboard:enquiry_detail", args=[o.pk]), o.name, o.phone,
                           format_html(" · {}", o.email) if o.email else "")
        interest = format_html("{}<div class='small text-muted'>{}</div>",
                               o.course.title if o.course else "Not sure yet", o.get_kind_display())
        return [timezone.localtime(o.created_at).strftime("%d %b %Y %H:%M"), lead, interest, o.source_label,
                badge(o.status, o.get_status_display()),
                o.follow_up_on.strftime("%d %b") if o.follow_up_on else "—", o.assigned_to or "—"]

    def actions(self, o):
        return [
            {"label": "Open", "url": reverse("dashboard:enquiry_detail", args=[o.pk]), "icon": "pencil-square"},
            {"label": "WhatsApp", "url": f"https://wa.me/{o.whatsapp_phone}", "icon": "whatsapp"},
        ]


@staff_required("content")
def enquiry_detail(request, pk):
    enq = get_object_or_404(Enquiry.objects.select_related("course", "assigned_to", "user"), pk=pk)
    form = EnquiryUpdateForm(request.POST or None, instance=enq, actor=request.user)
    if request.method == "POST" and form.is_valid():
        changes = audit.diff_form(form)
        form.save()
        if changes:
            audit.log(request, "enquiry_update", enq, changes)
        messages.success(request, f"Lead “{enq.name}” updated.")
        return redirect("dashboard:enquiries")
    return render(request, "dashboard/enquiry_detail.html", {"enq": enq, "form": form, "section": "enquiries"})


@staff_required("content")
def enquiry_export(request):
    view = EnquiryList()
    view.setup(request)
    resp = HttpResponse(content_type="text/csv")
    resp["Content-Disposition"] = 'attachment; filename="enquiries.csv"'
    w = csv.writer(resp)
    w.writerow(["received", "name", "phone", "email", "course", "interest", "preferred_time", "message", "status",
                "follow_up_on", "owner", "notes", "utm_source", "utm_medium", "utm_campaign", "referrer", "source_page"])
    for e in view.get_queryset():
        w.writerow([timezone.localtime(e.created_at).isoformat(), e.name, e.phone, e.email, e.course.title if e.course else "",
                    e.get_kind_display(), e.preferred_time, e.message, e.get_status_display(), e.follow_up_on or "",
                    e.assigned_to.email if e.assigned_to else "", e.notes, e.utm_source, e.utm_medium, e.utm_campaign,
                    e.referrer, e.source_page])
    audit.log(request, "export", object_type="enquiries")
    return resp


# --------------------------------------------------------------------- email outbox & system health
from apps.core import ops  # noqa: E402
from apps.core.models import OutboundEmail  # noqa: E402


class EmailOutboxList(ManageListView):
    capability = "super"
    section = "system"
    model = OutboundEmail
    title = "Email delivery (outbox)"
    search_fields = ["subject", "to", "last_error"]
    filters = [("status", "Status", OutboundEmail.STATUS_CHOICES)]
    sort_options = {"-created": ("Newest", "-created_at")}
    default_sort = "-created"
    paginate_by = 50

    def base_queryset(self):
        return OutboundEmail.objects.all()

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        st = ops.email_status()
        if st.get("queue"):
            ctx["summary_html"] = format_html(
                "Worker: <strong>{}</strong> · {} waiting · {} failed",
                "running" if st["worker_ok"] else "NOT RUNNING", st["pending"], st["failed"])
        if OutboundEmail.objects.filter(status=OutboundEmail.FAILED).exists():
            ctx["extra_buttons"] = [("Retry all failed", reverse("dashboard:email_retry", args=[0]), "arrow-clockwise")]
        return ctx

    def columns(self):
        return ["Queued", "To", "Subject", "Status", "Attempts", "Last error"]

    def row(self, o):
        return [timezone.localtime(o.created_at).strftime("%d %b %H:%M"), ", ".join(o.to)[:60], o.subject[:70],
                badge({"pending": "pending", "sending": "pending", "sent": "paid", "failed": "failed"}[o.status], o.get_status_display()),
                o.attempts, o.last_error[:80]]

    def actions(self, o):
        if o.status == OutboundEmail.FAILED:
            return [{"label": "Retry", "url": reverse("dashboard:email_retry", args=[o.pk]), "icon": "arrow-clockwise", "post": {"x": "1"}}]
        return []


@staff_required("super")
@require_POST
def email_retry(request, pk):
    qs = OutboundEmail.objects.filter(status=OutboundEmail.FAILED)
    if pk:
        qs = qs.filter(pk=pk)
    n = qs.update(status=OutboundEmail.PENDING, attempts=0, next_attempt_at=timezone.now(), last_error="")
    audit.log(request, "email_retry", object_type="outboundemail", object_repr=f"{n} email(s)")
    messages.success(request, f"{n} email(s) queued for another attempt.")
    return redirect("dashboard:email_outbox")


# --------------------------------------------------------------------- testimonials, FAQs, newsletter
from apps.core.models import FAQ, NewsletterSubscriber, Testimonial  # noqa: E402

from .forms import FAQForm, TestimonialForm  # noqa: E402


class TestimonialList(ManageListView):
    capability = "content"
    section = "website"
    model = Testimonial
    title = "Testimonials"
    search_fields = ["name", "company", "quote"]
    filters = [("is_published", "Published", [("true", "Published"), ("false", "Hidden")]),
               ("is_featured", "On home page", [("true", "Yes"), ("false", "No")])]
    sort_options = {"order": ("Display order", ["order", "-created_at"]), "-created": ("Newest", "-created_at")}
    default_sort = "order"
    create_url = "dashboard:testimonial_add"
    create_label = "Add testimonial"
    edit_url = "dashboard:testimonial_edit"
    delete_url = "dashboard:testimonial_delete"

    def base_queryset(self):
        return Testimonial.objects.select_related("course")

    def columns(self):
        return ["Person", "Quote", "Rating", "Course", "Home page", "Published"]

    def row(self, o):
        who = format_html("<strong>{}</strong><div class='small text-muted'>{}</div>", o.name,
                          " · ".join(x for x in [o.role, o.company] if x))
        return [who, o.quote[:90] + ("…" if len(o.quote) > 90 else ""), "★" * o.rating,
                o.course.title if o.course else "—", badge(o.is_featured), badge(o.is_published)]


class TestimonialCreate(ManageCreateView):
    capability = "content"
    section = "website"
    model = Testimonial
    form_class = TestimonialForm
    success_url_name = "dashboard:testimonials"


class TestimonialUpdate(ManageUpdateView):
    capability = "content"
    section = "website"
    model = Testimonial
    form_class = TestimonialForm
    success_url_name = "dashboard:testimonials"


class TestimonialDelete(ManageDeleteView):
    capability = "content"
    section = "website"
    model = Testimonial
    success_url_name = "dashboard:testimonials"


class FAQList(ManageListView):
    capability = "content"
    section = "website"
    model = FAQ
    title = "FAQs"
    search_fields = ["question", "answer"]
    filters = [("course_id", "Course", lambda v: [(c.pk, c.title) for c in Course.objects.all()]),
               ("is_corporate", "Corporate page", [("true", "Yes"), ("false", "No")])]
    sort_options = {"order": ("Display order", ["order", "id"])}
    default_sort = "order"
    create_url = "dashboard:faq_add"
    create_label = "Add FAQ"
    edit_url = "dashboard:faq_edit"
    delete_url = "dashboard:faq_delete"

    def base_queryset(self):
        return FAQ.objects.select_related("course")

    def columns(self):
        return ["Question", "Shown on", "Order", "Published"]

    def row(self, o):
        where = o.course.title if o.course else ("Corporate page" if o.is_corporate else "Home page")
        if o.course is None and o.is_corporate:
            where = "Home + Corporate"
        return [o.question, where, o.order, badge(o.is_published)]


class FAQCreate(ManageCreateView):
    capability = "content"
    section = "website"
    model = FAQ
    form_class = FAQForm
    success_url_name = "dashboard:faqs"


class FAQUpdate(ManageUpdateView):
    capability = "content"
    section = "website"
    model = FAQ
    form_class = FAQForm
    success_url_name = "dashboard:faqs"


class FAQDelete(ManageDeleteView):
    capability = "content"
    section = "website"
    model = FAQ
    success_url_name = "dashboard:faqs"


class SubscriberList(ManageListView):
    capability = "content"
    section = "website"
    model = NewsletterSubscriber
    title = "Newsletter subscribers"
    search_fields = ["email"]
    filters = [("is_active", "Status", [("true", "Subscribed"), ("false", "Unsubscribed")])]
    sort_options = {"-created": ("Newest", "-created_at")}
    default_sort = "-created"
    paginate_by = 50

    def base_queryset(self):
        return NewsletterSubscriber.objects.all()

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["extra_buttons"] = [("Export CSV", reverse("dashboard:subscriber_export"), "download")]
        return ctx

    def columns(self):
        return ["Email", "Subscribed", "Signed up on page", "Status"]

    def row(self, o):
        return [o.email, timezone.localtime(o.created_at).strftime("%d %b %Y"), o.source_page or "—",
                badge(o.is_active, "Subscribed" if o.is_active else "Unsubscribed")]

    def actions(self, o):
        return []


@staff_required("content")
def subscriber_export(request):
    resp = HttpResponse(content_type="text/csv")
    resp["Content-Disposition"] = 'attachment; filename="newsletter_subscribers.csv"'
    w = csv.writer(resp)
    w.writerow(["email", "subscribed_on", "source_page"])
    for s in NewsletterSubscriber.objects.filter(is_active=True).order_by("created_at"):
        w.writerow([s.email, timezone.localtime(s.created_at).date().isoformat(), s.source_page])
    audit.log(request, "export", object_type="newsletter")
    return resp
