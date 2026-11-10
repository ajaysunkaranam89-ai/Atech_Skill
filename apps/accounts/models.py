from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models

from apps.core.india import STATE_CHOICES
from apps.core.validators import validate_image_upload


class Role(models.TextChoices):
    SUPER_ADMIN = "super_admin", "Super Admin"
    COURSE_ADMIN = "course_admin", "Course Administrator"
    TRAINER = "trainer", "Trainer"
    STUDENT = "student", "Student"


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email, password, **extra):
        if not email:
            raise ValueError("Email is required")
        email = self.normalize_email(email).lower()
        user = self.model(email=email, **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra):
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        extra.setdefault("role", Role.STUDENT)
        return self._create_user(email, password, **extra)

    def create_superuser(self, email, password=None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        extra.setdefault("role", Role.SUPER_ADMIN)
        return self._create_user(email, password, **extra)


class User(AbstractUser):
    username = None
    email = models.EmailField("email address", unique=True)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.STUDENT, db_index=True)
    phone = models.CharField(max_length=20, blank=True)
    # Self-registered students start unverified; accounts created by administrators are trusted.
    email_verified = models.BooleanField(default=True)

    # Two-factor authentication (authenticator app) - required for staff in production
    totp_secret = models.CharField(max_length=64, blank=True, editable=False)
    totp_enabled = models.BooleanField(default=False)
    totp_last_counter = models.BigIntegerField(default=0, editable=False)
    totp_recovery_codes = models.JSONField(default=list, blank=True, editable=False)
    totp_enabled_at = models.DateTimeField(null=True, blank=True, editable=False)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()

    class Meta:
        ordering = ["email"]

    def __str__(self):
        return self.get_full_name() or self.email

    def save(self, *args, **kwargs):
        self.email = (self.email or "").lower()
        # Staff flag mirrors role so admin-only views can rely on it.
        self.is_staff = self.role != Role.STUDENT or self.is_superuser
        super().save(*args, **kwargs)

    # ---- role helpers -------------------------------------------------
    @property
    def is_super_admin(self):
        return self.is_active and (self.is_superuser or self.role == Role.SUPER_ADMIN)

    @property
    def is_course_admin(self):
        return self.is_active and self.role == Role.COURSE_ADMIN

    @property
    def is_trainer(self):
        return self.is_active and self.role == Role.TRAINER

    @property
    def is_student(self):
        return self.role == Role.STUDENT

    @property
    def is_academy_staff(self):
        return self.is_active and (self.is_superuser or self.role in {Role.SUPER_ADMIN, Role.COURSE_ADMIN, Role.TRAINER})

    @property
    def can_manage_all_content(self):
        return self.is_super_admin or self.is_course_admin

    @property
    def display_name(self):
        return self.get_full_name() or self.email.split("@")[0]


class StudentProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="student_profile")
    date_of_birth = models.DateField(null=True, blank=True)
    city = models.CharField(max_length=100, blank=True)
    qualification = models.CharField(max_length=150, blank=True)
    current_role = models.CharField(max_length=150, blank=True)
    linkedin_url = models.URLField(blank=True)
    photo = models.ImageField(upload_to="profiles/", blank=True, validators=[validate_image_upload])
    # Billing details printed on invoices (state decides CGST+SGST vs IGST).
    state = models.CharField("State", max_length=2, choices=STATE_CHOICES, blank=True)
    billing_name = models.CharField(max_length=200, blank=True, help_text="Company name, if your employer is paying")
    billing_gstin = models.CharField("GSTIN", max_length=15, blank=True, help_text="Only if you need a B2B GST invoice")
    billing_address = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Student: {self.user}"


class TrainerProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="trainer_profile")
    designation = models.CharField(max_length=150, blank=True)
    bio = models.TextField(
        blank=True,
        help_text="Formatting: '## Heading', '### Sub-heading', '* bullet', '1. numbered', **bold**. "
        "Leave a blank line between paragraphs. The first paragraph is shown on course pages.",
    )
    expertise = models.CharField(max_length=255, blank=True, help_text="Comma separated skills")
    experience_years = models.PositiveSmallIntegerField(default=0)
    photo = models.ImageField(upload_to="trainers/", blank=True, validators=[validate_image_upload])
    linkedin_url = models.URLField(blank=True)
    intro_video_url = models.URLField(blank=True, help_text="YouTube link to a short (1–2 min) introduction video")
    is_public = models.BooleanField(default=True, help_text="Show on public course pages")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["user__first_name", "user__last_name"]

    def __str__(self):
        return str(self.user)

    @property
    def intro_embed_url(self):
        from apps.courses.models import extract_youtube_id

        yid = extract_youtube_id(self.intro_video_url)
        return f"https://www.youtube-nocookie.com/embed/{yid}?rel=0&modestbranding=1" if yid else None

    @property
    def expertise_list(self):
        return [x.strip() for x in (self.expertise or "").split(",") if x.strip()]
