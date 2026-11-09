"""Students, administrators, enrollments and payments."""
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.html import format_html
from django.views.decorators.http import require_POST

from apps.accounts.forms import AdminUserForm, TrainerProfileForm
from apps.accounts.models import Role, TrainerProfile
from apps.core import audit
from apps.courses.access import progress_for_courses
from apps.courses.models import Course
from apps.enrollments import services as pay
from apps.enrollments.models import (
    Enrollment,
    EnrollmentSource,
    EnrollmentStatus,
    Payment,
    PaymentGateway,
    PaymentStatus,
    UpiPaymentSubmission,
    UpiStatus,
)

from .crud import ManageCreateView, ManageListView, ManageUpdateView, badge
from .decorators import staff_required
from .forms import EnrollmentForm, UpiReviewForm

User = get_user_model()


# --------------------------------------------------------------------- students
class StudentList(ManageListView):
    capability = "content"
    section = "students"
    model = User
    title = "Student management"
    search_fields = ["email", "first_name", "last_name", "phone"]
    filters = [("is_active", "Active", [("true", "Active"), ("false", "Disabled")])]
    sort_options = {"-joined": ("Newest", "-date_joined"), "name": ("Name", ["first_name", "last_name"]),
                    "-enrollments": ("Most enrollments", "-n_enr")}
    default_sort = "-joined"
    create_url = "dashboard:student_add"
    create_label = "Add student"

    def base_queryset(self):
        return User.objects.filter(role=Role.STUDENT).annotate(
            n_enr=Count("enrollments", filter=Q(enrollments__status=EnrollmentStatus.ACTIVE), distinct=True)
        )

    def columns(self):
        return ["Student", "Phone", "Active enrollments", "Joined", "Status"]

    def row(self, o):
        return [format_html('<a href="{}" class="fw-semibold">{}</a><div class="small text-muted">{}</div>',
                            reverse("dashboard:student_detail", args=[o.pk]), o.get_full_name() or "—", o.email),
                o.phone or "—", o.n_enr, o.date_joined.strftime("%d %b %Y"), badge(o.is_active, "Active" if o.is_active else "Disabled")]

    def actions(self, o):
        return [{"label": "Details", "url": reverse("dashboard:student_detail", args=[o.pk]), "icon": "person-lines-fill"},
                {"label": "Edit", "url": reverse("dashboard:student_edit", args=[o.pk]), "icon": "pencil"}]


class StudentCreate(ManageCreateView):
    capability = "content"
    section = "students"
    model = User
    form_class = AdminUserForm
    success_url_name = "dashboard:students"
    title = "Add student"

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        form.fields["role"].choices = [(Role.STUDENT, "Student")]
        return form


class StudentUpdate(ManageUpdateView):
    capability = "content"
    section = "students"
    model = User
    form_class = AdminUserForm
    success_url_name = "dashboard:students"

    def get_queryset(self):
        return User.objects.filter(role=Role.STUDENT)


@staff_required("content")
def student_detail(request, pk):
    student = get_object_or_404(User, pk=pk, role=Role.STUDENT)
    enrollments = list(Enrollment.objects.filter(student=student).select_related("course", "batch"))
    prog = progress_for_courses(student, [e.course for e in enrollments if e.course])
    for e in enrollments:
        e.progress = prog.get(e.course_id, {}) if e.course_id else {}
    return render(request, "dashboard/student_detail.html", {
        "student": student, "enrollments": enrollments, "section": "students",
        "payments": Payment.objects.filter(student=student).order_by("-created_at"),
        "upi": UpiPaymentSubmission.objects.filter(student=student),
        "certificates": student.certificates.all(),
        "submissions": student.submissions.select_related("assignment")[:20],
        "batches": student.batch_memberships.select_related("batch__course"),
    })


# --------------------------------------------------------------------- staff / administrators
class StaffList(ManageListView):
    capability = "super"
    section = "admins"
    model = User
    title = "Administrators & trainers"
    search_fields = ["email", "first_name", "last_name"]
    filters = [("role", "Role", [c for c in Role.choices if c[0] != Role.STUDENT])]
    sort_options = {"name": ("Name", ["first_name", "last_name"]), "role": ("Role", "role")}
    default_sort = "role"
    create_url = "dashboard:staff_add"
    create_label = "Add administrator / trainer"
    edit_url = "dashboard:staff_edit"

    def base_queryset(self):
        return User.objects.exclude(role=Role.STUDENT)

    def columns(self):
        return ["Name", "Email", "Role", "Last login", "Status"]

    def row(self, o):
        return [o.get_full_name() or "—", o.email, o.get_role_display(),
                o.last_login.strftime("%d %b %Y %H:%M") if o.last_login else "never",
                badge(o.is_active, "Active" if o.is_active else "Disabled")]

    def actions(self, o):
        acts = super().actions(o)
        if o.role == Role.TRAINER:
            acts.append({"label": "Trainer profile", "url": reverse("dashboard:trainer_profile", args=[o.pk]), "icon": "person-badge"})
        return acts


class StaffCreate(ManageCreateView):
    capability = "super"
    section = "admins"
    model = User
    form_class = AdminUserForm
    success_url_name = "dashboard:staff"
    title = "Add administrator / trainer"

    def get_initial(self):
        return {"role": Role.TRAINER, "is_active": True}


class StaffUpdate(ManageUpdateView):
    capability = "super"
    section = "admins"
    model = User
    form_class = AdminUserForm
    success_url_name = "dashboard:staff"

    def get_queryset(self):
        return User.objects.exclude(role=Role.STUDENT)


@staff_required("super")
def trainer_profile(request, pk):
    user = get_object_or_404(User, pk=pk, role=Role.TRAINER)
    tp, _ = TrainerProfile.objects.get_or_create(user=user)
    form = TrainerProfileForm(request.POST or None, request.FILES or None, instance=tp)
    if request.method == "POST" and form.is_valid():
        form.save()
        audit.log(request, "update", tp, audit.diff_form(form))
        messages.success(request, "Trainer profile saved.")
        return redirect("dashboard:staff")
    return render(request, "dashboard/form.html", {"form": form, "title": f"Trainer profile: {user}", "section": "admins",
                                                   "cancel_url": reverse("dashboard:staff")})


# --------------------------------------------------------------------- enrollments
class EnrollmentList(ManageListView):
    capability = "super"
    section = "enrollments"
    model = Enrollment
    title = "Enrollment management"
    search_fields = ["student__email", "student__first_name", "student__last_name", "course_title"]
    filters = [
        ("course_id", "Course", lambda v: [(c.pk, c.title) for c in Course.objects.all()]),
        ("status", "Status", EnrollmentStatus.choices),
        ("source", "Source", EnrollmentSource.choices),
    ]
    sort_options = {"-created": ("Newest", "-created_at"), "-activated": ("Recently activated", "-activated_at")}
    default_sort = "-created"
    create_url = "dashboard:enrollment_add"
    create_label = "Enroll a student"
    edit_url = "dashboard:enrollment_edit"

    def base_queryset(self):
        return Enrollment.objects.select_related("student", "course", "batch")

    def columns(self):
        return ["Student", "Course", "Batch", "Source", "Status", "Activated", "Expires"]

    def row(self, o):
        return [format_html("{}<div class='small text-muted'>{}</div>", o.student.get_full_name(), o.student.email),
                o.course_title or (o.course.title if o.course else "—"), o.batch.code if o.batch else "—",
                o.get_source_display() or "—", badge(o.status, o.get_status_display()),
                o.activated_at.strftime("%d %b %Y") if o.activated_at else "—",
                o.expires_at.strftime("%d %b %Y") if o.expires_at else "Lifetime"]


class EnrollmentCreate(ManageCreateView):
    capability = "super"
    section = "enrollments"
    model = Enrollment
    form_class = EnrollmentForm
    success_url_name = "dashboard:enrollments"

    def form_valid(self, form):
        status = form.cleaned_data["status"]
        expires = form.cleaned_data.get("expires_at")
        form.instance.source = EnrollmentSource.ADMIN
        form.instance.status = EnrollmentStatus.PENDING
        response = super().form_valid(form)
        if status == EnrollmentStatus.ACTIVE:
            pay.activate_enrollment(self.object.student, self.object.course, EnrollmentSource.ADMIN, batch=self.object.batch)
            if expires:
                Enrollment.objects.filter(pk=self.object.pk).update(expires_at=expires)
        else:
            Enrollment.objects.filter(pk=self.object.pk).update(status=status)
        return response


class EnrollmentUpdate(ManageUpdateView):
    capability = "super"
    section = "enrollments"
    model = Enrollment
    form_class = EnrollmentForm
    success_url_name = "dashboard:enrollments"

    def form_valid(self, form):
        if "status" in form.changed_data and form.cleaned_data["status"] == EnrollmentStatus.ACTIVE and not form.instance.activated_at:
            from django.utils import timezone

            form.instance.activated_at = timezone.now()
        response = super().form_valid(form)
        if self.object.batch_id:
            self.object.batch.add_student(self.object.student, force=True)  # capacity validated by the form
        return response


# --------------------------------------------------------------------- payments
class PaymentList(ManageListView):
    capability = "super"
    section = "payments"
    model = Payment
    title = "Payment management"
    search_fields = ["student__email", "razorpay_order_id", "razorpay_payment_id", "upi_reference", "course_title"]
    filters = [("status", "Status", PaymentStatus.choices), ("gateway", "Gateway", PaymentGateway.choices),
               ("course_id", "Course", lambda v: [(c.pk, c.title) for c in Course.objects.all()])]
    sort_options = {"-created": ("Newest", "-created_at"), "-amount": ("Amount ↓", "-amount")}
    default_sort = "-created"

    def base_queryset(self):
        return Payment.objects.select_related("student")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        pending = UpiPaymentSubmission.objects.filter(status=UpiStatus.PENDING).count()
        ctx["extra_buttons"] = [(f"UPI verification ({pending} pending)", reverse("dashboard:upi_list"), "qr-code")]
        ctx["summary"] = self.get_queryset().filter(status=PaymentStatus.PAID).aggregate(total=Sum("amount"), n=Count("id"))
        return ctx

    def columns(self):
        return ["Date", "Student", "Course", "Gateway", "Amount (₹)", "Reference", "Status"]

    def row(self, o):
        return [o.created_at.strftime("%d %b %Y %H:%M"), o.student.email, o.course_title or "—", o.get_gateway_display(),
                o.amount, o.razorpay_payment_id or o.upi_reference or o.razorpay_order_id or "—", badge(o.status, o.get_status_display())]

    def actions(self, o):
        if o.status == PaymentStatus.PAID:
            return [{"label": "Invoice", "url": reverse("portal:invoice_pdf", args=[o.pk]), "icon": "file-earmark-pdf"}]
        return []


class UpiList(ManageListView):
    capability = "super"
    section = "payments"
    model = UpiPaymentSubmission
    title = "UPI payment verification"
    search_fields = ["utr_number", "student__email", "payer_name", "course_title"]
    filters = [("status", "Status", UpiStatus.choices)]
    sort_options = {"-created": ("Newest", "-created_at"), "created": ("Oldest first", "created_at")}
    default_sort = "created"

    def base_queryset(self):
        return UpiPaymentSubmission.objects.select_related("student", "course")

    def get_queryset(self):
        qs = super().get_queryset()
        if "status" not in self.request.GET:
            qs = qs.filter(status=UpiStatus.PENDING)
        return qs

    def columns(self):
        return ["Submitted", "Student", "Course", "Amount (₹)", "Expected (₹)", "UTR", "Status"]

    def row(self, o):
        expected = o.course.effective_price if o.course else None
        amt = format_html('<span class="text-danger fw-bold">{}</span>', o.amount) if expected is not None and o.amount != expected else o.amount
        return [o.created_at.strftime("%d %b %Y %H:%M"), o.student.email, o.course_title, amt, expected or "—", o.utr_number,
                badge(o.status, o.get_status_display())]

    def actions(self, o):
        return [{"label": "Review", "url": reverse("dashboard:upi_review", args=[o.pk]), "icon": "check2-square"}]


@staff_required("super")
def upi_review(request, pk):
    sub = get_object_or_404(UpiPaymentSubmission.objects.select_related("student", "course"), pk=pk)
    form = UpiReviewForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if sub.status != UpiStatus.PENDING:
            messages.warning(request, "This submission was already reviewed.")
            return redirect("dashboard:upi_list")
        note = form.cleaned_data["note"]
        if form.cleaned_data["decision"] == "approve":
            pay.approve_upi(sub, request.user, note)
            audit.log(request, "upi_approve", sub, {"utr": sub.utr_number, "amount": str(sub.amount)})
            messages.success(request, f"UPI payment {sub.utr_number} approved and enrollment activated.")
        else:
            pay.reject_upi(sub, request.user, note)
            audit.log(request, "upi_reject", sub, {"utr": sub.utr_number, "note": note})
            messages.info(request, f"UPI payment {sub.utr_number} rejected.")
        return redirect("dashboard:upi_list")
    return render(request, "dashboard/upi_review.html", {"sub": sub, "form": form, "section": "payments"})


@staff_required("super")
@require_POST
def enrollment_cancel(request, pk):
    e = get_object_or_404(Enrollment, pk=pk)
    old = e.status
    e.status = EnrollmentStatus.CANCELLED
    e.save()
    audit.log(request, "enrollment_cancel", e, {"status": [old, e.status]})
    messages.success(request, "Enrollment cancelled. Payment history is kept.")
    return redirect(request.POST.get("next") or "dashboard:enrollments")
