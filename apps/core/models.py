from django.conf import settings
from django.core.cache import cache
from django.core.files.storage import FileSystemStorage
from django.db import models
from django.utils import timezone
from django.utils.deconstruct import deconstructible

from .india import STATE_CHOICES
from .validators import validate_image_upload


@deconstructible
class PrivateStorage(FileSystemStorage):
    """Files outside MEDIA_ROOT that are never directly reachable by URL."""

    def __init__(self, **kwargs):
        kwargs.setdefault("location", settings.PRIVATE_MEDIA_ROOT)
        kwargs.setdefault("base_url", None)
        super().__init__(**kwargs)

    @property
    def base_location(self):
        return settings.PRIVATE_MEDIA_ROOT

    @property
    def location(self):
        return str(settings.PRIVATE_MEDIA_ROOT)


private_storage = PrivateStorage()


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class SiteSettings(models.Model):
    """Singleton editable from Admin Dashboard > Website Settings."""

    CACHE_KEY = "site_settings_v1"

    site_name = models.CharField(max_length=120, default="A TechSkills Academy")
    tagline = models.CharField(max_length=200, default="Learn. Practice. Build Your IT Career.")
    hero_title = models.CharField(max_length=200, default="Job-ready IT training, taught live by practitioners")
    hero_subtitle = models.TextField(
        default="Live Google Meet classes, recorded lessons you can rewatch, hands-on assignments and certificates."
    )
    about_text = models.TextField(blank=True)
    logo = models.ImageField(upload_to="site/", blank=True, validators=[validate_image_upload])
    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=30, blank=True)
    whatsapp_number = models.CharField(max_length=30, blank=True)
    address = models.TextField(blank=True)
    facebook_url = models.URLField(blank=True)
    instagram_url = models.URLField(blank=True)
    linkedin_url = models.URLField(blank=True)
    youtube_url = models.URLField(blank=True)

    # Payments
    razorpay_enabled = models.BooleanField(default=True, help_text="Requires RAZORPAY_* keys in the environment.")
    upi_enabled = models.BooleanField(default=True)
    upi_id = models.CharField(max_length=100, blank=True, help_text="e.g. academy@okicici")
    upi_payee_name = models.CharField(max_length=120, blank=True)
    upi_qr_code = models.ImageField(upload_to="site/", blank=True, validators=[validate_image_upload])
    upi_instructions = models.TextField(
        blank=True,
        default="Pay the exact course fee to the UPI ID above, then submit the UTR / transaction reference "
        "and a screenshot. Your enrollment is activated after the academy verifies the payment.",
    )

    # Notifications
    email_notifications_enabled = models.BooleanField(default=True)
    require_email_verification = models.BooleanField(
        default=True,
        help_text="Students who sign up must confirm their email before enrolling. "
        "Turn off only if outgoing email (SMTP) is not configured yet.",
    )

    footer_text = models.CharField(max_length=255, blank=True)

    # Home page trust strip (shown only when filled; enter real figures only)
    stat_students = models.CharField("Students trained", max_length=20, blank=True, help_text="e.g. 2,500+")
    stat_years = models.CharField("Years of training", max_length=20, blank=True, help_text="e.g. 10+")
    stat_batches = models.CharField("Batches completed", max_length=20, blank=True, help_text="e.g. 150+")
    stat_placement = models.CharField("Placement / success rate", max_length=20, blank=True, help_text="e.g. 90%")
    stat_rating = models.CharField("Average rating", max_length=20, blank=True, help_text="e.g. 4.8/5")
    certification_paths = models.TextField(
        blank=True, help_text="Certifications your courses prepare for, one per line (e.g. Cisco CCNA, AWS Solutions Architect)"
    )
    topbar_text = models.CharField(
        "Top bar announcement", max_length=120, blank=True, help_text="Short message in the top bar, e.g. 'New AWS batch starts 1 Dec'"
    )
    corporate_intro = models.TextField(
        "Corporate training intro", blank=True,
        help_text="Shown on the Corporate Training page. Formatting: '## Heading', '* bullet'. Leave blank for the standard text.",
    )
    corporate_clients = models.TextField(
        blank=True, help_text="Organisations you have trained, one per line (only with their permission)"
    )

    # SEO & analytics
    seo_home_title = models.CharField(
        "Home page SEO title", max_length=70, blank=True,
        help_text="e.g. 'IT Training Institute in Bangalore | CCNA, AWS, DevOps, Kubernetes'",
    )
    seo_home_description = models.CharField("Home page SEO description", max_length=170, blank=True)
    city = models.CharField(max_length=80, blank=True, help_text="City you serve, used in search snippets and local SEO")
    google_site_verification = models.CharField(
        max_length=100, blank=True, help_text="Google Search Console > HTML tag > content value only"
    )
    google_analytics_id = models.CharField(
        "Google Analytics 4 ID", max_length=20, blank=True, help_text="e.g. G-ABC123XYZ. Leave blank to disable."
    )

    # Legal / policy pages (required by Razorpay for account activation).
    # Leave blank to publish the standard template filled with your contact details.
    POLICY_HELP = "Leave blank to use the standard template. Formatting: '## Heading', '* bullet', '**bold**'."
    legal_business_name = models.CharField(
        max_length=200, blank=True, help_text="Registered business / proprietor name shown on policy pages"
    )
    privacy_policy = models.TextField(blank=True, help_text=POLICY_HELP)
    terms_conditions = models.TextField("Terms & conditions", blank=True, help_text=POLICY_HELP)
    refund_policy = models.TextField("Refund & cancellation policy", blank=True, help_text=POLICY_HELP)
    # Invoicing / GST
    INVOICE_MODE_GST = "gst"
    INVOICE_MODE_RECEIPT = "receipt"
    invoice_mode = models.CharField(
        max_length=10,
        choices=[(INVOICE_MODE_RECEIPT, "Without GST (Payment Receipt)"), (INVOICE_MODE_GST, "With GST (Tax Invoice)")],
        default=INVOICE_MODE_RECEIPT,
        help_text="Applies to payments verified from now on. Already-issued invoices never change.",
    )
    gstin = models.CharField(
        "GSTIN", max_length=15, blank=True, help_text="Required for 'With GST' mode. You can keep it saved in either mode.",
    )
    business_state = models.CharField(max_length=2, choices=STATE_CHOICES, blank=True, help_text="State of registration")
    gst_rate = models.DecimalField(max_digits=5, decimal_places=2, default=18, help_text="GST % included in course prices")
    sac_code = models.CharField("SAC code", max_length=10, default="999293", help_text="999293 = commercial training & coaching")
    invoice_prefix = models.CharField(max_length=10, default="ATSA", help_text="Invoice numbers look like ATSA/2026-27/0001")
    refund_window_days = models.PositiveSmallIntegerField(
        default=7, help_text="Used by the standard refund policy template"
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Website settings"
        verbose_name_plural = "Website settings"

    def __str__(self):
        return "Website settings"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)
        cache.delete(self.CACHE_KEY)

    @classmethod
    def load(cls):
        obj = cache.get(cls.CACHE_KEY)
        if obj is None:
            obj, _ = cls.objects.get_or_create(pk=1)
            cache.set(cls.CACHE_KEY, obj, 300)
        return obj


class AuditLog(models.Model):
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="audit_logs"
    )
    action = models.CharField(max_length=60, db_index=True)
    object_type = models.CharField(max_length=60, db_index=True)
    object_id = models.CharField(max_length=40, blank=True)
    object_repr = models.CharField(max_length=255, blank=True)
    changes = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.actor} {self.action} {self.object_type}#{self.object_id}"


class EnquiryStatus(models.TextChoices):
    NEW = "new", "New"
    CONTACTED = "contacted", "Contacted"
    DEMO = "demo_scheduled", "Demo scheduled"
    CONVERTED = "converted", "Converted (enrolled)"
    CLOSED = "closed", "Not interested / closed"


class Enquiry(models.Model):
    """A lead captured from the public site (course page, contact page, demo request)."""

    KIND_CHOICES = [("demo", "Free demo"), ("callback", "Call back"), ("question", "Question"), ("batch", "Batch timings"),
                    ("corporate", "Corporate training proposal"), ("brochure", "Brochure download")]

    name = models.CharField(max_length=120)
    phone = models.CharField(max_length=20)
    email = models.EmailField(blank=True)
    course = models.ForeignKey("courses.Course", null=True, blank=True, on_delete=models.SET_NULL, related_name="enquiries")
    kind = models.CharField("I'm interested in", max_length=20, choices=KIND_CHOICES, default="demo")
    message = models.TextField(blank=True)
    preferred_time = models.CharField(max_length=60, blank=True)
    company_name = models.CharField(max_length=150, blank=True)
    designation = models.CharField(max_length=100, blank=True)
    team_size = models.CharField(max_length=20, blank=True)
    training_mode = models.CharField(max_length=30, blank=True)
    status = models.CharField(max_length=20, choices=EnquiryStatus.choices, default=EnquiryStatus.NEW, db_index=True)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="assigned_enquiries"
    )
    notes = models.TextField(blank=True, help_text="Internal follow-up notes")
    follow_up_on = models.DateField(null=True, blank=True)
    # Attribution (first touch): where the visitor came from
    source_page = models.CharField(max_length=300, blank=True)
    referrer = models.CharField(max_length=300, blank=True)
    utm_source = models.CharField(max_length=100, blank=True)
    utm_medium = models.CharField(max_length=100, blank=True)
    utm_campaign = models.CharField(max_length=100, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "Enquiries"

    def __str__(self):
        return f"{self.name} ({self.phone})"

    @property
    def source_label(self):
        if self.utm_source:
            return " / ".join(x for x in [self.utm_source, self.utm_medium, self.utm_campaign] if x)
        if self.referrer:
            from urllib.parse import urlparse

            host = urlparse(self.referrer).netloc.replace("www.", "")
            return host or "Referral"
        return "Direct"

    @property
    def whatsapp_phone(self):
        digits = "".join(ch for ch in self.phone if ch.isdigit())
        return ("91" + digits) if len(digits) == 10 else digits


class OutboundEmail(models.Model):
    """Queued outgoing email (see apps.core.mail)."""

    PENDING, SENDING, SENT, FAILED = "pending", "sending", "sent", "failed"
    STATUS_CHOICES = [(PENDING, "Queued"), (SENDING, "Sending"), (SENT, "Sent"), (FAILED, "Failed")]

    subject = models.CharField(max_length=255)
    body = models.TextField(blank=True)
    html = models.TextField(blank=True)
    from_email = models.CharField(max_length=254)
    to = models.JSONField(default=list)
    cc = models.JSONField(default=list, blank=True)
    bcc = models.JSONField(default=list, blank=True)
    reply_to = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=PENDING, db_index=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    last_error = models.CharField(max_length=500, blank=True)
    next_attempt_at = models.DateTimeField(default=timezone.now, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.subject} -> {', '.join(self.to)}"


class Testimonial(models.Model):
    """Real student / client feedback, managed in Admin > Testimonials."""

    name = models.CharField(max_length=120)
    role = models.CharField("Role / designation", max_length=120, blank=True, help_text="e.g. Network Engineer")
    company = models.CharField(max_length=120, blank=True)
    photo = models.ImageField(upload_to="testimonials/", blank=True, validators=[validate_image_upload])
    quote = models.TextField(max_length=800)
    rating = models.PositiveSmallIntegerField(default=5, choices=[(i, f"{i} star{'s' if i > 1 else ''}") for i in range(5, 0, -1)])
    course = models.ForeignKey("courses.Course", null=True, blank=True, on_delete=models.SET_NULL, related_name="testimonials")
    is_published = models.BooleanField(default=True)
    is_featured = models.BooleanField(default=False, help_text="Show on the home page")
    order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["order", "-created_at"]

    def __str__(self):
        return f"{self.name} ({self.company})" if self.company else self.name


class FAQ(models.Model):
    """Frequently asked questions: general (home/contact) or per course."""

    question = models.CharField(max_length=255)
    answer = models.TextField(help_text="Formatting: '* bullet', '**bold**'")
    course = models.ForeignKey(
        "courses.Course", null=True, blank=True, on_delete=models.CASCADE, related_name="faqs",
        help_text="Leave blank for a general academy FAQ (home page)",
    )
    is_corporate = models.BooleanField(default=False, help_text="Show on the Corporate Training page")
    is_published = models.BooleanField(default=True)
    order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["order", "id"]
        verbose_name = "FAQ"
        verbose_name_plural = "FAQs"

    def __str__(self):
        return self.question


class NewsletterSubscriber(models.Model):
    email = models.EmailField(unique=True)
    source_page = models.CharField(max_length=300, blank=True)
    is_active = models.BooleanField(default=True)
    unsubscribe_token = models.CharField(max_length=40, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.email

    def save(self, *args, **kwargs):
        if not self.unsubscribe_token:
            import secrets

            self.unsubscribe_token = secrets.token_urlsafe(24)
        super().save(*args, **kwargs)


class WhyChooseItem(models.Model):
    """Cards in the home page "Why choose us" section (Admin > Why choose us)."""

    icon = models.CharField(max_length=40, default="bi-check2-circle",
                            help_text="Bootstrap icon name, e.g. bi-terminal, bi-award (see icons.getbootstrap.com)")
    title = models.CharField(max_length=80)
    text = models.CharField(max_length=220)
    order = models.PositiveIntegerField(default=0)
    is_published = models.BooleanField(default=True)

    class Meta:
        ordering = ["order", "id"]
        verbose_name = "Why choose us item"

    def __str__(self):
        return self.title
