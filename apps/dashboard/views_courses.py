"""Course, category and module management + course builder (drag & drop ordering)."""
import json

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.html import format_html
from django.views.decorators.http import require_POST

from apps.core import audit
from apps.courses.access import can_manage_course
from apps.courses.models import (
    ContentStatus,
    Course,
    CourseCategory,
    CourseModule,
    CourseStatus,
    Lesson,
)
from apps.enrollments.models import Enrollment

from .crud import (
    ManageCreateView,
    ManageDeleteView,
    ManageListView,
    ManageUpdateView,
    badge,
    has_cap,
)
from .decorators import staff_required
from .forms import CategoryForm, CourseForm, ModuleForm


# --------------------------------------------------------------------- categories
class CategoryList(ManageListView):
    capability = "content"
    section = "courses"
    model = CourseCategory
    title = "Course categories"
    search_fields = ["name"]
    sort_options = {"order": ("Display order", ["order", "name"]), "name": ("Name", "name")}
    default_sort = "order"
    create_url = "dashboard:category_add"
    edit_url = "dashboard:category_edit"
    delete_url = "dashboard:category_delete"

    def base_queryset(self):
        return CourseCategory.objects.annotate(n=Count("courses"))

    def columns(self):
        return ["Name", "Courses", "Order", "Active"]

    def row(self, o):
        return [o.name, o.n, o.order, badge(o.is_active)]


class CategoryCreate(ManageCreateView):
    capability = "content"
    section = "courses"
    model = CourseCategory
    form_class = CategoryForm
    success_url_name = "dashboard:categories"


class CategoryUpdate(ManageUpdateView):
    capability = "content"
    section = "courses"
    model = CourseCategory
    form_class = CategoryForm
    success_url_name = "dashboard:categories"


class CategoryDelete(ManageDeleteView):
    capability = "content"
    section = "courses"
    model = CourseCategory
    success_url_name = "dashboard:categories"
    warning = "Courses in this category will become uncategorised; they are not deleted."


# --------------------------------------------------------------------- courses
class CourseList(ManageListView):
    section = "courses"
    model = Course
    title = "Course management"
    search_fields = ["title", "short_description", "slug"]
    filters = [
        ("status", "Status", CourseStatus.choices),
        ("category_id", "Category", lambda v: [(c.pk, c.name) for c in CourseCategory.objects.all()]),
    ]
    sort_options = {
        "title": ("Title A–Z", "title"),
        "-updated": ("Recently updated", "-updated_at"),
        "-created": ("Newest", "-created_at"),
        "price": ("Price ↑", "price"),
        "-price": ("Price ↓", "-price"),
        "-students": ("Most students", "-students"),
    }
    default_sort = "-updated"
    edit_url = "dashboard:course_edit"
    delete_url = "dashboard:course_delete"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        if has_cap(self.request.user, "content"):
            ctx["create_url"] = reverse("dashboard:course_add")
            ctx["create_label"] = "Add course"
            ctx["extra_buttons"] = [("Categories", reverse("dashboard:categories"), "tags")]
        return ctx

    def base_queryset(self):
        return (
            Course.objects.managed_by(self.request.user)
            .select_related("category")
            .annotate(
                students=Count("enrollments", filter=Q(enrollments__status__in=["active", "completed"]), distinct=True),
                n_lessons=Count("modules__lessons", distinct=True),
            )
        )

    def columns(self):
        return ["Course", "Category", "Price (₹)", "Lessons", "Students", "Status", "Updated"]

    def row(self, o):
        thumb = format_html('<img src="{}" class="thumb-sm me-2" alt="">', o.thumbnail.url) if o.thumbnail else ""
        title = format_html(
            '{}<a href="{}" class="fw-semibold">{}</a><div class="small text-muted">{}</div>',
            thumb, reverse("dashboard:course_builder", args=[o.pk]), o.title, o.slug,
        )
        price = format_html("<s class='text-muted'>{}</s> {}", o.price, o.discounted_price) if o.discounted_price is not None else o.price
        return [title, o.category or "—", price, o.n_lessons, o.students, badge(o.status, o.get_status_display()),
                o.updated_at.strftime("%d %b %Y")]

    def actions(self, o):
        acts = [{"label": "Builder", "url": reverse("dashboard:course_builder", args=[o.pk]), "icon": "diagram-3"}]
        if has_cap(self.request.user, "content"):
            acts.append({"label": "Edit", "url": reverse("dashboard:course_edit", args=[o.pk]), "icon": "pencil"})
        acts.append({"label": "View", "url": o.get_absolute_url(), "icon": "eye"})
        if self.request.user.is_super_admin:
            acts.append({"label": "Delete", "url": reverse("dashboard:course_delete", args=[o.pk]), "icon": "trash", "danger": True})
        return acts


class CourseCreate(ManageCreateView):
    capability = "content"
    section = "courses"
    model = Course
    form_class = CourseForm
    upload_progress = True
    success_url_name = "dashboard:courses"

    def get_success_url(self):
        return reverse("dashboard:course_builder", args=[self.object.pk])


class CourseUpdate(ManageUpdateView):
    capability = "content"
    section = "courses"
    model = Course
    form_class = CourseForm
    upload_progress = True
    success_url_name = "dashboard:courses"


class CourseDelete(ManageDeleteView):
    capability = "super"
    section = "courses"
    model = Course
    success_url_name = "dashboard:courses"
    require_typed_confirmation = True
    warning = (
        "This permanently deletes the course, all its modules, lessons, videos, study materials, assignments, "
        "batches and live classes. Enrollment, payment and certificate records are KEPT for history. "
        "Consider Archive instead — it hides the course without losing anything."
    )

    def confirmation_text(self):
        return self.object.title

    def impact(self):
        c = self.object
        return [
            ("Modules", c.modules.count()),
            ("Lessons", Lesson.objects.filter(module__course=c).count()),
            ("Enrollments (kept)", Enrollment.objects.filter(course=c).count()),
            ("Batches (deleted)", c.batches.count()),
        ]


STATUS_ACTIONS = {
    "publish": CourseStatus.PUBLISHED,
    "unpublish": CourseStatus.UNPUBLISHED,
    "archive": CourseStatus.ARCHIVED,
    "draft": CourseStatus.DRAFT,
}


@staff_required("content")
@require_POST
def course_status(request, pk):
    course = get_object_or_404(Course, pk=pk)
    new = STATUS_ACTIONS.get(request.POST.get("action"))
    if not new:
        return HttpResponseBadRequest("Unknown action")
    old = course.status
    course.status = new
    course.save()
    audit.log(request, f"course_{request.POST['action']}", course, {"status": [old, new]})
    messages.success(request, f"“{course.title}” is now {course.get_status_display()}.")
    return redirect(request.POST.get("next") or reverse("dashboard:course_builder", args=[pk]))


# --------------------------------------------------------------------- builder & ordering
@staff_required("staff")
def course_builder(request, pk):
    course = get_object_or_404(Course, pk=pk)
    if not can_manage_course(request.user, course):
        raise PermissionDenied
    modules = list(course.modules.order_by("order", "id"))
    lessons = (
        Lesson.objects.filter(module__course=course)
        .select_related("active_video")
        .annotate(n_resources=Count("resources", distinct=True))
        .order_by("order", "id")
    )
    by_mod = {m.id: [] for m in modules}
    for lesson in lessons:
        by_mod[lesson.module_id].append(lesson)
    for m in modules:
        m.lesson_list = by_mod[m.id]
    return render(
        request,
        "dashboard/course_builder.html",
        {"course": course, "modules": modules, "section": "courses", "can_edit_course": has_cap(request.user, "content")},
    )


@staff_required("staff")
@require_POST
def course_reorder(request, pk):
    """Persist drag & drop order. Body: {"modules": [ids], "lessons": {"<module_id>": [ids]}}"""
    course = get_object_or_404(Course, pk=pk)
    if not can_manage_course(request.user, course):
        raise PermissionDenied
    try:
        data = json.loads(request.body or b"{}")
        module_ids = [int(x) for x in data.get("modules", [])]
        lesson_map = {int(k): [int(x) for x in v] for k, v in (data.get("lessons") or {}).items()}
    except (ValueError, TypeError, AttributeError):
        return JsonResponse({"ok": False, "error": "Invalid payload"}, status=400)

    own_modules = set(course.modules.values_list("id", flat=True))
    own_lessons = set(Lesson.objects.filter(module__course=course).values_list("id", flat=True))
    if not set(module_ids) <= own_modules or not set(lesson_map) <= own_modules:
        return JsonResponse({"ok": False, "error": "Module does not belong to this course"}, status=400)
    all_lessons = [lid for ids in lesson_map.values() for lid in ids]
    if not set(all_lessons) <= own_lessons or len(all_lessons) != len(set(all_lessons)):
        return JsonResponse({"ok": False, "error": "Lesson does not belong to this course"}, status=400)

    with transaction.atomic():
        for i, mid in enumerate(module_ids, start=1):
            CourseModule.objects.filter(pk=mid).update(order=i)
        for mid, ids in lesson_map.items():
            for i, lid in enumerate(ids, start=1):
                Lesson.objects.filter(pk=lid).update(order=i, module_id=mid)
    audit.log(request, "reorder", course, {"modules": module_ids, "lessons": {str(k): v for k, v in lesson_map.items()}})
    return JsonResponse({"ok": True})


# --------------------------------------------------------------------- modules
class ModuleScopeMixin:
    def get_queryset(self):
        return CourseModule.objects.filter(course__in=Course.objects.managed_by(self.request.user)).select_related("course")


class ModuleList(ManageListView):
    section = "modules"
    model = CourseModule
    title = "Module management"
    search_fields = ["title", "course__title"]
    filters = [
        ("course_id", "Course", lambda v: [(c.pk, c.title) for c in Course.objects.managed_by(v.request.user)]),
        ("status", "Status", ContentStatus.choices),
    ]
    sort_options = {"course": ("Course & order", ["course__title", "order"]), "-updated": ("Recently updated", "-updated_at")}
    default_sort = "course"
    create_url = "dashboard:module_add"
    create_label = "Add module"
    edit_url = "dashboard:module_edit"
    delete_url = "dashboard:module_delete"

    def base_queryset(self):
        return (
            CourseModule.objects.filter(course__in=Course.objects.managed_by(self.request.user))
            .select_related("course")
            .annotate(n=Count("lessons"))
        )

    def columns(self):
        return ["Module", "Course", "Order", "Lessons", "Status"]

    def row(self, o):
        return [o.title, format_html('<a href="{}">{}</a>', reverse("dashboard:course_builder", args=[o.course_id]), o.course.title),
                o.order, o.n, badge(o.status, o.get_status_display())]


class ModuleCreate(ManageCreateView):
    section = "modules"
    model = CourseModule
    form_class = ModuleForm
    success_url_name = "dashboard:modules"

    def get_initial(self):
        init = super().get_initial()
        if self.request.GET.get("course"):
            init["course"] = self.request.GET["course"]
        return init

    def get_success_url(self):
        return reverse("dashboard:course_builder", args=[self.object.course_id])

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["cancel_url"] = reverse("dashboard:modules")
        return ctx


class ModuleUpdate(ModuleScopeMixin, ManageUpdateView):
    section = "modules"
    model = CourseModule
    form_class = ModuleForm
    success_url_name = "dashboard:modules"

    def get_success_url(self):
        return reverse("dashboard:course_builder", args=[self.object.course_id])


class ModuleDelete(ModuleScopeMixin, ManageDeleteView):
    section = "modules"
    model = CourseModule
    success_url_name = "dashboard:modules"
    require_typed_confirmation = True
    warning = "All lessons, videos and materials inside this module will be deleted. Student progress for these lessons is removed; enrollments and payments are not affected."

    def confirmation_text(self):
        return self.object.title

    def impact(self):
        return [("Lessons", self.object.lessons.count())]


@staff_required("staff")
@require_POST
def module_status(request, pk):
    module = get_object_or_404(CourseModule, pk=pk)
    if not can_manage_course(request.user, module.course):
        raise PermissionDenied
    new = request.POST.get("status")
    if new not in ContentStatus.values:
        return HttpResponseBadRequest("Bad status")
    old = module.status
    module.status = new
    module.save()
    audit.log(request, "module_status", module, {"status": [old, new]})
    messages.success(request, f"Module “{module.title}” is now {module.get_status_display()}.")
    return redirect(reverse("dashboard:course_builder", args=[module.course_id]))
