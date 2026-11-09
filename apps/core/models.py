from django.conf import settings
from django.core.cache import cache
from django.core.files.storage import FileSystemStorage
from django.db import models
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
