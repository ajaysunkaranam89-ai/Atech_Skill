"""Search-engine helpers: sitemap, robots.txt and schema.org structured data."""
from urllib.parse import urlparse

from django.conf import settings
from django.contrib.sitemaps import Sitemap
from django.http import HttpResponse
from django.urls import reverse
from django.views.decorators.cache import cache_page

from apps.accounts.models import TrainerProfile
from apps.courses.models import Course, CourseStatus

class _Base(Sitemap):
    """Always advertise the public SITE_URL (read per request), not the internal Host header."""

    def get_protocol(self, protocol=None):
        return urlparse(settings.SITE_URL).scheme or "https"

    def get_domain(self, site=None):
        return urlparse(settings.SITE_URL).netloc or super().get_domain(site)


class StaticSitemap(_Base):
    changefreq = "weekly"

    def items(self):
        return ["core:home", "core:course_list", "core:about", "core:trainers", "core:corporate", "core:contact",
                "core:terms", "core:privacy", "core:refund"]

    def location(self, item):
        return reverse(item)

    def priority(self, item):
        return {"core:home": 1.0, "core:course_list": 0.9}.get(item, 0.5)


class CourseSitemap(_Base):
    changefreq = "weekly"
    priority = 0.9

    def items(self):
        return Course.objects.filter(status=CourseStatus.PUBLISHED).order_by("pk")

    def lastmod(self, obj):
        return obj.updated_at


class TrainerSitemap(_Base):
    changefreq = "monthly"
    priority = 0.6

    def items(self):
        return TrainerProfile.objects.filter(is_public=True, user__is_active=True).order_by("pk")

    def location(self, obj):
        return reverse("core:trainer_detail", args=[obj.pk])


SITEMAPS = {"static": StaticSitemap, "courses": CourseSitemap, "trainers": TrainerSitemap}

PRIVATE_PATHS = ["/dashboard/", "/learn/", "/accounts/", "/payments/", "/notifications/", "/api/", "/django-admin/"]


@cache_page(60 * 60)
def robots_txt(request):
    lines = ["User-agent: *"] + [f"Disallow: {p}" for p in PRIVATE_PATHS]
    lines += ["Allow: /", "", f"Sitemap: {settings.SITE_URL}/sitemap.xml"]
    return HttpResponse("\n".join(lines) + "\n", content_type="text/plain")


# ------------------------------------------------------------------ schema.org JSON-LD
def abs_url(path):
    return settings.SITE_URL + path if path.startswith("/") else path


def organization_ld(site, logo_url):
    data = {
        "@context": "https://schema.org",
        "@type": "EducationalOrganization",
        "name": site.site_name,
        "url": settings.SITE_URL + "/",
        "logo": abs_url(logo_url),
        "slogan": site.tagline,
    }
    if site.legal_business_name:
        data["legalName"] = site.legal_business_name
    if site.address or site.city:
        data["address"] = {"@type": "PostalAddress", "addressCountry": "IN",
                           **({"streetAddress": " ".join(site.address.split())} if site.address else {}),
                           **({"addressLocality": site.city} if site.city else {})}
    if site.city:
        data["areaServed"] = [site.city, "India"]
    if site.contact_email or site.contact_phone:
        data["contactPoint"] = {"@type": "ContactPoint", "contactType": "customer support",
                                **({"email": site.contact_email} if site.contact_email else {}),
                                **({"telephone": site.contact_phone} if site.contact_phone else {})}
    same_as = [u for u in (site.linkedin_url, site.youtube_url, site.instagram_url, site.facebook_url) if u]
    if same_as:
        data["sameAs"] = same_as
    return data


def course_ld(course, site, trainers, image_url=None):
    data = {
        "@context": "https://schema.org",
        "@type": "Course",
        "name": course.title,
        "description": course.short_description or course.description[:300],
        "url": abs_url(course.get_absolute_url()),
        "inLanguage": "en" if course.language.lower().startswith("english") else course.language,
        "provider": {"@type": "Organization", "name": site.site_name, "sameAs": settings.SITE_URL + "/"},
        "offers": [{
            "@type": "Offer",
            "category": "Free" if course.is_free else "Paid",
            "price": str(course.effective_price),
            "priceCurrency": "INR",
            "availability": "https://schema.org/InStock",
            "url": abs_url(course.get_absolute_url()),
        }],
        "hasCourseInstance": [{
            "@type": "CourseInstance",
            "courseMode": "Online",
            "courseWorkload": f"PT{course.duration_hours}H" if course.duration_hours else None,
            "instructor": [{"@type": "Person", "name": t.user.get_full_name(),
                            **({"description": t.designation} if t.designation else {})} for t in trainers] or None,
        }],
    }
    inst = data["hasCourseInstance"][0]
    for k in [k for k, v in inst.items() if v is None]:
        del inst[k]
    if image_url:
        data["image"] = abs_url(image_url)
    if course.objectives_list:
        data["teaches"] = course.objectives_list[:10]
    if course.prerequisites_list:
        data["coursePrerequisites"] = course.prerequisites_list[:10]
    if course.category:
        data["about"] = course.category.name
    return data


def trainer_ld(trainer, site):
    data = {
        "@context": "https://schema.org",
        "@type": "Person",
        "name": trainer.user.get_full_name(),
        "jobTitle": trainer.designation or "Trainer",
        "worksFor": {"@type": "EducationalOrganization", "name": site.site_name, "url": settings.SITE_URL + "/"},
        "url": abs_url(reverse("core:trainer_detail", args=[trainer.pk])),
    }
    if trainer.photo:
        data["image"] = abs_url(trainer.photo.url)
    if trainer.expertise_list:
        data["knowsAbout"] = trainer.expertise_list
    if trainer.linkedin_url:
        data["sameAs"] = [trainer.linkedin_url]
    return data


def breadcrumb_ld(items):
    return {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [{"@type": "ListItem", "position": i, "name": name, "item": abs_url(url)}
                            for i, (name, url) in enumerate(items, start=1)],
    }


def faq_ld(faqs):
    return {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [{"@type": "Question", "name": f.question,
                        "acceptedAnswer": {"@type": "Answer", "text": f.answer}} for f in faqs],
    }
