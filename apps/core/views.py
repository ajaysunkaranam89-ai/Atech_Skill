from django.core.paginator import Paginator
from django.db import connection
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.cache import never_cache

from apps.courses.access import can_manage_course, course_progress, student_outline, valid_enrollment, full_outline
from apps.courses.models import Batch, BatchStatus, Certificate, Course, CourseCategory, CourseStatus


def home(request):
    published = Course.objects.published().select_related("category")
    ctx = {
        "featured": published.filter(is_featured=True)[:6] or published[:6],
        "categories": CourseCategory.objects.filter(is_active=True, courses__status=CourseStatus.PUBLISHED)
        .annotate(n=Count("courses", filter=Q(courses__status=CourseStatus.PUBLISHED)))
        .distinct(),
        "course_count": published.count(),
    }
    return render(request, "core/home.html", ctx)


def course_list(request):
    qs = Course.objects.published().select_related("category").prefetch_related("trainers__user")
    q = request.GET.get("q", "").strip()
    cat = request.GET.get("category", "")
    level = request.GET.get("level", "")
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(short_description__icontains=q) | Q(syllabus__icontains=q))
    if cat:
        qs = qs.filter(category__slug=cat)
    if level:
        qs = qs.filter(level=level)
    sort = request.GET.get("sort", "")
    qs = qs.order_by({"price": "price", "-price": "-price", "new": "-published_at"}.get(sort, "-is_featured"), "title")
    page = Paginator(qs, 12).get_page(request.GET.get("page"))
    return render(
        request,
        "core/course_list.html",
        {
            "page": page,
            "categories": CourseCategory.objects.filter(is_active=True),
            "levels": Course._meta.get_field("level").choices,
            "q": q, "cat": cat, "level": level, "sort": sort,
        },
    )


def course_detail(request, slug):
    course = get_object_or_404(Course.objects.select_related("category"), slug=slug)
    is_manager = request.user.is_authenticated and can_manage_course(request.user, course)
    if course.status != CourseStatus.PUBLISHED and not is_manager:
        # Enrolled students can still reach the learning portal for unpublished/archived
        # courses they bought; the public page itself is hidden.
        from django.http import Http404

        raise Http404("Course not found")
    enrollment = valid_enrollment(request.user, course) if request.user.is_authenticated else None
    outline = full_outline(course) if is_manager else student_outline(course)
    batches = course.batches.filter(
        is_open_for_enrollment=True, status__in=[BatchStatus.UPCOMING, BatchStatus.ONGOING]
    ).order_by("start_date")
    ctx = {
        "course": course,
        "outline": outline,
        "lesson_count": sum(len(m.visible_lessons) for m in outline),
        "enrollment": enrollment,
        "progress": course_progress(request.user, course) if enrollment else None,
        "trainers": course.trainers.filter(is_public=True).select_related("user"),
        "batches": batches,
        "preview": is_manager and course.status != CourseStatus.PUBLISHED,
    }
    return render(request, "core/course_detail.html", ctx)


def certificate_verify(request, number):
    cert = Certificate.objects.filter(certificate_number=number.upper()).select_related("student").first()
    return render(request, "core/certificate_verify.html", {"cert": cert, "number": number})


@never_cache
def healthz(request):
    try:
        with connection.cursor() as c:
            c.execute("SELECT 1")
        return JsonResponse({"status": "ok", "database": "ok"})
    except Exception:
        return JsonResponse({"status": "error", "database": "unavailable"}, status=503)


def error_403(request, exception=None):
    return render(request, "errors/403.html", status=403)


def error_404(request, exception=None):
    return render(request, "errors/404.html", status=404)
