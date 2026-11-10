from django.core.paginator import Paginator
from django.db import connection
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.templatetags.static import static
from django.urls import reverse
from django.views.decorators.cache import never_cache

from . import seo
from .models import SiteSettings

from apps.courses.access import can_manage_course, course_progress, student_outline, valid_enrollment, full_outline
from apps.courses.models import Batch, BatchStatus, Certificate, Course, CourseCategory, CourseStatus


def home(request):
    published = Course.objects.published().with_next_batch().select_related("category")
    ctx = {
        "featured": published.filter(is_featured=True)[:6] or published[:6],
        "categories": CourseCategory.objects.filter(is_active=True, courses__status=CourseStatus.PUBLISHED)
        .annotate(n=Count("courses", filter=Q(courses__status=CourseStatus.PUBLISHED)))
        .distinct(),
        "course_count": published.count(),
        "structured_data": seo.organization_ld(SiteSettings.load(), static("img/logo-full.png")),
    }
    from .models import FAQ, Testimonial

    site = SiteSettings.load()
    ctx["testimonials"] = Testimonial.objects.filter(is_published=True, is_featured=True).select_related("course")[:6]
    ctx["faqs"] = list(FAQ.objects.filter(is_published=True, course__isnull=True)[:8])
    ctx["cert_paths"] = [c.strip() for c in site.certification_paths.splitlines() if c.strip()]
    ctx["stats"] = [(v, label) for v, label in [
        (site.stat_students, "Students trained"), (site.stat_years, "Years of training"),
        (site.stat_batches, "Batches completed"), (site.stat_placement, "Placement success"),
        (site.stat_rating, "Average rating")] if v]
    ctx["why_us"] = [
        ("bi-person-video3", "Live classes with practitioners", "Learn from trainers who run enterprise networks, servers and cloud platforms every day."),
        ("bi-terminal", "Hands-on labs", "Configure routers, servers, AWS and Kubernetes yourself - not just slides."),
        ("bi-collection-play", "Recordings for every class", "Missed a session or need revision? Every lesson stays available in your portal."),
        ("bi-patch-check", "Certification-focused", "Syllabus aligned to industry certifications, with practice questions and guidance."),
        ("bi-briefcase", "Career support", "Resume tips, interview preparation and real-world scenarios that employers ask about."),
        ("bi-award", "Verifiable certificates", "Every certificate has a QR code employers can scan to confirm it's genuine."),
    ]
    if ctx["faqs"]:
        ctx["structured_data"] = [ctx["structured_data"], seo.faq_ld(ctx["faqs"])]
    return render(request, "core/home.html", ctx)


def course_list(request):
    qs = Course.objects.published().with_next_batch().select_related("category").prefetch_related("trainers__user")
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
    ).annotate(taken=Count("batch_students")).order_by("start_date")
    trainers = list(course.trainers.filter(is_public=True).select_related("user"))
    crumbs = [("Courses", reverse("core:course_list"))]
    if course.category:
        crumbs.append((course.category.name, reverse("core:course_list") + f"?category={course.category.slug}"))
    crumbs.append((course.title, course.get_absolute_url()))
    from .models import FAQ, Testimonial

    course_faqs = list(FAQ.objects.filter(is_published=True, course=course))
    ctx = {
        "course_faqs": course_faqs,
        "course_testimonials": Testimonial.objects.filter(is_published=True, course=course)[:4],
        "structured_data": [
            seo.course_ld(course, SiteSettings.load(), trainers, course.thumbnail.url if course.thumbnail else None),
            seo.breadcrumb_ld(crumbs),
        ],
        "course": course,
        "outline": outline,
        "lesson_count": sum(len(m.visible_lessons) for m in outline),
        "enrollment": enrollment,
        "progress": course_progress(request.user, course) if enrollment else None,
        "trainers": trainers,
        "batches": batches,
        "preview": is_manager and course.status != CourseStatus.PUBLISHED,
    }
    return render(request, "core/course_detail.html", ctx)


def trainer_detail(request, pk):
    from apps.accounts.models import TrainerProfile

    trainer = get_object_or_404(TrainerProfile.objects.select_related("user"), pk=pk, is_public=True, user__is_active=True)
    return render(request, "core/trainer_detail.html", {
        "structured_data": seo.trainer_ld(trainer, SiteSettings.load()),
        "trainer": trainer,
        "courses": trainer.courses.filter(status=CourseStatus.PUBLISHED).select_related("category"),
    })


POLICY_TITLES = {
    "privacy": "Privacy Policy",
    "terms": "Terms & Conditions",
    "refund": "Refund & Cancellation Policy",
}


def policy_page(request, kind):
    from django.conf import settings as dj_settings

    from .models import SiteSettings
    from .policies import render_policy

    body = render_policy(kind, SiteSettings.load(), dj_settings.SITE_URL)
    return render(request, "core/policy.html", {"title": POLICY_TITLES[kind], "body": body, "kind": kind})


def trainers(request):
    from apps.accounts.models import TrainerProfile

    items = (
        TrainerProfile.objects.filter(is_public=True, user__is_active=True)
        .select_related("user")
        .annotate(n_courses=Count("courses", filter=Q(courses__status=CourseStatus.PUBLISHED)))
        .order_by("-experience_years", "user__first_name")
    )
    return render(request, "core/trainers.html", {"items": items})


def about(request):
    from apps.accounts.models import TrainerProfile

    site = SiteSettings.load()
    return render(request, "core/about.html", {
        "trainers": TrainerProfile.objects.filter(is_public=True, user__is_active=True).select_related("user")[:6],
        "course_count": Course.objects.published().count(),
        "categories": CourseCategory.objects.filter(is_active=True, courses__status=CourseStatus.PUBLISHED).distinct(),
        "structured_data": seo.organization_ld(site, static("img/logo-full.png")),
    })


def contact(request):
    return render(request, "core/contact.html")


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


ENQUIRY_LIMIT_PER_HOUR = 5


def enquiry_submit(request):
    """Public lead form (course pages, contact page, home). POST only; GET shows the standalone form."""
    from django.contrib import messages
    from django.core.cache import cache
    from django.shortcuts import redirect
    from django.utils.http import url_has_allowed_host_and_scheme

    from apps.notifications.models import NotificationKind
    from apps.notifications.services import notify_users

    from .audit import client_ip
    from .forms import EnquiryForm

    nxt = request.POST.get("next") or request.GET.get("next") or ""
    if not url_has_allowed_host_and_scheme(nxt, {request.get_host()}, request.is_secure()):
        nxt = ""
    initial = {}
    if request.user.is_authenticated:
        initial = {"name": request.user.get_full_name(), "email": request.user.email, "phone": request.user.phone}
    if request.GET.get("course"):
        initial["course"] = request.GET["course"]

    if request.method != "POST":
        return render(request, "core/enquiry.html", {"form": EnquiryForm(initial=initial), "next": nxt})

    ip = client_ip(request) or "unknown"
    key = f"enquiry-rate:{ip}"
    count = cache.get(key, 0)
    form = EnquiryForm(request.POST)
    if count >= ENQUIRY_LIMIT_PER_HOUR:
        form.is_valid()
        form.add_error(None, "Too many enquiries from your network. Please call or WhatsApp us instead.")
        return render(request, "core/enquiry.html", {"form": form, "next": nxt}, status=429)
    if not form.is_valid():
        return render(request, "core/enquiry.html", {"form": form, "next": nxt}, status=400)

    enq = form.save(commit=False)
    src = request.session.get("lead_src", {})
    enq.utm_source = src.get("utm_source", "")
    enq.utm_medium = src.get("utm_medium", "")
    enq.utm_campaign = src.get("utm_campaign", "")
    enq.referrer = src.get("referrer", "")
    enq.source_page = (nxt or request.META.get("HTTP_REFERER", ""))[:300]
    enq.ip_address = ip if ip != "unknown" else None
    enq.user = request.user if request.user.is_authenticated else None
    enq.save()
    cache.set(key, count + 1, 3600)

    from django.contrib.auth import get_user_model

    staff = get_user_model().objects.filter(is_active=True, role__in=["super_admin", "course_admin"])
    what = enq.get_kind_display() + (f" – {enq.course.title}" if enq.course else "")
    notify_users(
        staff, NotificationKind.ANNOUNCEMENT, f"New lead: {enq.name} ({what})",
        f"Phone: {enq.phone}\nEmail: {enq.email or '-'}\nBest time: {enq.preferred_time or '-'}\n"
        f"Source: {enq.source_label}\n\n{enq.message}",
        link=f"/dashboard/enquiries/{enq.pk}/", dedupe_key=f"enquiry:{enq.pk}",
    )
    if enq.kind == "brochure" and enq.course:
        from django.http import HttpResponse

        from .pdf import brochure_pdf

        resp = HttpResponse(brochure_pdf(enq.course, SiteSettings.load()), content_type="application/pdf")
        resp["Content-Disposition"] = f'attachment; filename="{enq.course.slug}-brochure.pdf"'
        return resp
    messages.success(request, f"Thank you, {enq.name.split()[0]}! Our team will contact you on {enq.phone} shortly.")
    return redirect((nxt or "/") + "#enquiry-thanks")



def corporate_training(request):
    from .forms import EnquiryForm
    from .models import FAQ

    site = SiteSettings.load()
    return render(request, "core/corporate.html", {
        "form": EnquiryForm(initial={"kind": "corporate"}, auto_id="corp_%s"),
        "courses": Course.objects.published().select_related("category")[:12],
        "clients": [c.strip() for c in site.corporate_clients.splitlines() if c.strip()],
        "faqs": FAQ.objects.filter(is_published=True, is_corporate=True),
    })


def newsletter_subscribe(request):
    from django.contrib import messages
    from django.core.cache import cache
    from django.shortcuts import redirect
    from django.utils.http import url_has_allowed_host_and_scheme

    from .audit import client_ip
    from .forms import NewsletterForm
    from .models import NewsletterSubscriber

    nxt = request.POST.get("next", "/")
    if not url_has_allowed_host_and_scheme(nxt, {request.get_host()}, request.is_secure()):
        nxt = "/"
    if request.method != "POST":
        return redirect(nxt)
    key = f"nl-rate:{client_ip(request)}"
    form = NewsletterForm(request.POST)
    if cache.get(key, 0) >= 5 or not form.is_valid() or form.cleaned_data.get("website"):
        messages.error(request, "Please enter a valid email address.")
        return redirect(nxt + "#newsletter")
    cache.set(key, cache.get(key, 0) + 1, 3600)
    sub, created = NewsletterSubscriber.objects.get_or_create(
        email=form.cleaned_data["email"].lower(), defaults={"source_page": nxt[:300]})
    if not sub.is_active:
        sub.is_active = True
        sub.save(update_fields=["is_active"])
    messages.success(request, "Thanks for subscribing! You'll hear about new batches and free workshops first.")
    return redirect(nxt + "#newsletter")


def newsletter_unsubscribe(request, token):
    from .models import NewsletterSubscriber

    sub = NewsletterSubscriber.objects.filter(unsubscribe_token=token).first()
    if sub and request.method == "POST":
        sub.is_active = False
        sub.save(update_fields=["is_active"])
    return render(request, "core/unsubscribe.html", {"sub": sub, "done": request.method == "POST"})
