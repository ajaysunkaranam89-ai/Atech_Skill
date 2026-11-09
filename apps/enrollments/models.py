from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import TimeStampedModel, private_storage
from apps.core.validators import validate_image_upload


class EnrollmentStatus(models.TextChoices):
    PENDING = "pending", "Pending payment"
    ACTIVE = "active", "Active"
    COMPLETED = "completed", "Completed"
    EXPIRED = "expired", "Expired"
    CANCELLED = "cancelled", "Cancelled"


class EnrollmentSource(models.TextChoices):
    RAZORPAY = "razorpay", "Razorpay"
    UPI = "upi", "Manual UPI (verified)"
    ADMIN = "admin", "Added by administrator"
    FREE = "free", "Free course"


class Enrollment(TimeStampedModel):
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="enrollments")
    # SET_NULL + snapshot keeps historical enrollment records if a course is ever deleted.
    course = models.ForeignKey(
        "courses.Course", null=True, blank=True, on_delete=models.SET_NULL, related_name="enrollments"
    )
    course_title = models.CharField(max_length=200, blank=True)
    batch = models.ForeignKey(
        "courses.Batch", null=True, blank=True, on_delete=models.SET_NULL, related_name="enrollments"
    )
    status = models.CharField(max_length=20, choices=EnrollmentStatus.choices, default=EnrollmentStatus.PENDING, db_index=True)
    source = models.CharField(max_length=20, choices=EnrollmentSource.choices, blank=True)
    activated_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["student", "course"], name="uniq_enrollment_student_course")]

    def __str__(self):
        return f"{self.student} → {self.course_title or self.course}"

    def save(self, *args, **kwargs):
        if self.course_id and not self.course_title:
            self.course_title = self.course.title
        super().save(*args, **kwargs)

    @property
    def is_valid(self):
        if self.status not in (EnrollmentStatus.ACTIVE, EnrollmentStatus.COMPLETED):
            return False
        return not (self.expires_at and self.expires_at < timezone.now())

    def activate(self, source, batch=None):
        """Idempotently activate access. Safe to call repeatedly (webhook + callback)."""
        now = timezone.now()
        if self.status not in (EnrollmentStatus.ACTIVE, EnrollmentStatus.COMPLETED) or not self.is_valid:
            self.status = EnrollmentStatus.ACTIVE
            self.activated_at = now
            days = self.course.access_days if self.course_id else 0
            self.expires_at = now + timedelta(days=days) if days else None
        self.source = self.source or source
        target = batch if batch is not None else self.batch
        if target is not None:
            if target.add_student(self.student):
                self.batch = target
            else:
                # Paid, but the batch filled up in the meantime: keep access, flag for the admin.
                self.batch = None
                self._batch_was_full = target
                note = f"Batch {target.code} was full at activation - assign a batch manually."
                self.notes = (self.notes + " " + note).strip()[:255]
        self.save()
        return self


class PaymentStatus(models.TextChoices):
    CREATED = "created", "Created"
    PAID = "paid", "Paid"
    FAILED = "failed", "Failed"
    REFUNDED = "refunded", "Refunded"


class PaymentGateway(models.TextChoices):
    RAZORPAY = "razorpay", "Razorpay"
    UPI = "upi", "Manual UPI"
    OFFLINE = "offline", "Offline / cash"


class Payment(TimeStampedModel):
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="payments")
    course = models.ForeignKey("courses.Course", null=True, blank=True, on_delete=models.SET_NULL, related_name="payments")
    course_title = models.CharField(max_length=200, blank=True)
    enrollment = models.ForeignKey(Enrollment, null=True, blank=True, on_delete=models.SET_NULL, related_name="payments")
    batch = models.ForeignKey("courses.Batch", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    gateway = models.CharField(max_length=20, choices=PaymentGateway.choices)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default="INR")
    status = models.CharField(max_length=20, choices=PaymentStatus.choices, default=PaymentStatus.CREATED, db_index=True)
    razorpay_order_id = models.CharField(max_length=64, unique=True, null=True, blank=True)
    razorpay_payment_id = models.CharField(max_length=64, blank=True, db_index=True)
    razorpay_signature = models.CharField(max_length=128, blank=True)
    upi_reference = models.CharField(max_length=64, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    failure_reason = models.CharField(max_length=255, blank=True)
    raw_response = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_gateway_display()} ₹{self.amount} – {self.student} – {self.get_status_display()}"

    def save(self, *args, **kwargs):
        if self.course_id and not self.course_title:
            self.course_title = self.course.title
        super().save(*args, **kwargs)

    @property
    def amount_paise(self):
        return int(round(self.amount * 100))


class UpiStatus(models.TextChoices):
    PENDING = "pending", "Pending verification"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"


class UpiPaymentSubmission(TimeStampedModel):
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="upi_submissions")
    course = models.ForeignKey("courses.Course", null=True, on_delete=models.SET_NULL, related_name="upi_submissions")
    course_title = models.CharField(max_length=200, blank=True)
    batch = models.ForeignKey("courses.Batch", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    utr_number = models.CharField("UTR / transaction reference", max_length=40, unique=True)
    payer_name = models.CharField(max_length=120)
    payer_upi_id = models.CharField(max_length=100, blank=True)
    paid_on = models.DateField()
    screenshot = models.ImageField(
        upload_to="upi/%Y/%m/", storage=private_storage, blank=True, validators=[validate_image_upload]
    )
    status = models.CharField(max_length=20, choices=UpiStatus.choices, default=UpiStatus.PENDING, db_index=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.CharField(max_length=255, blank=True)
    payment = models.OneToOneField(Payment, null=True, blank=True, on_delete=models.SET_NULL, related_name="upi_submission")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"UPI {self.utr_number} – {self.student} – {self.get_status_display()}"

    def save(self, *args, **kwargs):
        self.utr_number = (self.utr_number or "").strip().upper()
        if self.course_id and not self.course_title:
            self.course_title = self.course.title
        super().save(*args, **kwargs)
