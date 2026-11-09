from django import forms
from django.contrib.auth import get_user_model

from apps.accounts.forms import BootstrapMixin
from apps.accounts.models import Role, TrainerProfile
from apps.core.models import SiteSettings
from apps.core.validators import validate_document_upload, validate_image_upload, validate_video_upload
from apps.courses.models import (
    Assignment,
    AssignmentSubmission,
    Batch,
    Certificate,
    ContentStatus,
    Course,
    CourseCategory,
    CourseModule,
    Lesson,
    LessonResource,
    LiveClass,
    ResourceType,
    VideoAsset,
    VideoSource,
)
from apps.enrollments.models import Enrollment, EnrollmentStatus
from apps.notifications.models import Announcement
from apps.notifications.services import Audience

User = get_user_model()

DT = forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M")
D = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


class StyledModelForm(BootstrapMixin, forms.ModelForm):
    def __init__(self, *args, **kwargs):
        self.actor = kwargs.pop("actor", None)
        super().__init__(*args, **kwargs)
        self.limit_querysets()
        self._bootstrap()

    def limit_querysets(self):
        pass

    def courses_for_actor(self):
        qs = Course.objects.all()
        return qs.managed_by(self.actor) if self.actor else qs


class OptionalNumbersMixin:
    """Treat blank duration/order inputs as 0 instead of failing validation."""

    optional_numbers = ("duration_minutes", "order")

    def _relax_numbers(self):
        for name in self.optional_numbers:
            if name in self.fields:
                self.fields[name].required = False

    def clean_duration_minutes(self):
        return self.cleaned_data.get("duration_minutes") or 0

    def clean_order(self):
        return self.cleaned_data.get("order") or 0


class NotifyMixin(forms.Form):
    """Adds 'who to notify' controls to content forms."""

    notify = forms.ChoiceField(choices=Audience.CHOICES, initial=Audience.SILENT, label="Notify students")
    notify_batches = forms.ModelMultipleChoiceField(
        queryset=Batch.objects.none(), required=False, label="Batches to notify",
        help_text="Used only when 'Notify selected batches' is chosen.",
        widget=forms.SelectMultiple(attrs={"size": 4}),
    )
    notify_email = forms.BooleanField(required=False, initial=True, label="Also send email")

    def setup_notify(self, course=None, default=Audience.SILENT):
        qs = Batch.objects.select_related("course")
        if course is not None:
            qs = qs.filter(course=course)
        elif self.actor is not None:
            qs = qs.filter(course__in=Course.objects.managed_by(self.actor))
        self.fields["notify_batches"].queryset = qs
        self.fields["notify_batches"].label_from_instance = lambda b: f"{b.course.title} – {b.code}"
        if not self.is_bound:
            self.initial.setdefault("notify", default)

    def clean(self):
        cd = super().clean()
        if cd.get("notify") == Audience.BATCHES and not cd.get("notify_batches"):
            self.add_error("notify_batches", "Select at least one batch, or choose a different notify option.")
        return cd


# ----------------------------------------------------------------- courses
class CategoryForm(StyledModelForm):
    class Meta:
        model = CourseCategory
        fields = ["name", "description", "icon", "order", "is_active"]
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}


class CourseForm(StyledModelForm):
    class Meta:
        model = Course
        fields = [
            "title", "category", "short_description", "description", "thumbnail", "promo_video_url",
            "price", "discounted_price", "duration_text", "duration_hours", "level", "mode", "language",
            "syllabus", "learning_objectives", "prerequisites", "target_audience",
            "trainers", "access_days", "is_featured", "certificate_enabled", "status",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 6}),
            "syllabus": forms.Textarea(attrs={"rows": 6}),
            "learning_objectives": forms.Textarea(attrs={"rows": 4}),
            "prerequisites": forms.Textarea(attrs={"rows": 3}),
            "target_audience": forms.Textarea(attrs={"rows": 3}),
            "trainers": forms.SelectMultiple(attrs={"size": 5}),
        }

    new_category = forms.CharField(
        max_length=120, required=False, label="…or create a new category",
        help_text="Type a name (e.g. DevOps, Cloud, Python) to create it and use it for this course.",
    )

    fieldsets = [
        ("Basics", ["title", "category", "new_category", "short_description", "description", "thumbnail", "promo_video_url"]),
        ("Pricing", ["price", "discounted_price", "access_days"]),
        ("Format", ["duration_text", "duration_hours", "level", "mode", "language"]),
        ("Curriculum", ["syllabus", "learning_objectives", "prerequisites", "target_audience"]),
        ("Trainers & publishing", ["trainers", "is_featured", "certificate_enabled", "status"]),
    ]

    def limit_querysets(self):
        self.fields["trainers"].queryset = TrainerProfile.objects.select_related("user").filter(user__is_active=True)
        cats = CourseCategory.objects.filter(is_active=True)
        self.fields["category"].queryset = cats
        if not cats.exists():
            self.fields["category"].empty_label = "No categories yet — create one below"
        else:
            self.fields["category"].empty_label = "— Select a category —"
        if not self.fields["trainers"].queryset.exists():
            self.fields["trainers"].help_text = (
                "No trainers yet. Add them under Administrators (role: Trainer); you can assign them later."
            )

    def clean(self):
        cd = super().clean()
        name = (cd.get("new_category") or "").strip()
        if name and cd.get("category"):
            self.add_error("new_category", "Choose an existing category or type a new one, not both.")
        return cd

    def save(self, commit=True):
        name = (self.cleaned_data.get("new_category") or "").strip()
        if name:
            cat = CourseCategory.objects.filter(name__iexact=name).first()
            if cat is None:
                cat = CourseCategory.objects.create(name=name)
            elif not cat.is_active:
                cat.is_active = True
                cat.save(update_fields=["is_active"])
            self.instance.category = cat
        return super().save(commit=commit)


class ModuleForm(OptionalNumbersMixin, StyledModelForm):
    class Meta:
        model = CourseModule
        fields = ["course", "title", "description", "order", "status"]
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}

    def limit_querysets(self):
        self.fields["course"].queryset = self.courses_for_actor()
        self._relax_numbers()
        self.fields["order"].help_text = "Leave 0 to add at the end. You can also drag & drop in the course builder."


def module_choice_label(m):
    return f"{m.course.title} › {m.title}"


def lesson_choice_label(lesson):
    return f"{lesson.module.course.title} › {lesson.module.title} › {lesson.title}"


class LessonForm(OptionalNumbersMixin, NotifyMixin, StyledModelForm):
    """Edit lesson details (video is managed separately with versioning)."""

    class Meta:
        model = Lesson
        fields = ["module", "title", "description", "duration_minutes", "order", "is_free_preview", "status"]
        widgets = {"description": forms.Textarea(attrs={"rows": 5})}

    def limit_querysets(self):
        self.fields["module"].queryset = CourseModule.objects.filter(course__in=self.courses_for_actor()).select_related("course")
        self.fields["module"].label_from_instance = module_choice_label
        self._relax_numbers()
        course = self.instance.module.course if self.instance.pk else None
        self.setup_notify(course)


class RecordedVideoCreateForm(OptionalNumbersMixin, NotifyMixin, StyledModelForm):
    """Spec 4A: one form that creates a lesson together with its first video."""

    course = forms.ModelChoiceField(queryset=Course.objects.none(), help_text="Filters the module list")
    source_type = forms.ChoiceField(
        choices=VideoSource.choices + [("", "No video yet (text / PDF lesson)")],
        initial=VideoSource.YOUTUBE, label="Video source", required=False,
    )
    video_url = forms.URLField(required=False, label="Video URL", help_text="YouTube unlisted/watch/embed URL or an HTTPS video URL")
    video_file = forms.FileField(required=False, label="Upload MP4", validators=[validate_video_upload])
    thumbnail = forms.ImageField(required=False, validators=[validate_image_upload])
    duration_seconds = forms.IntegerField(min_value=0, required=False, label="Video duration (seconds)")
    pdf_notes = forms.FileField(required=False, label="PDF notes (optional)", validators=[validate_document_upload])
    publish = forms.ChoiceField(
        choices=[(ContentStatus.DRAFT, "Save as draft"), (ContentStatus.PUBLISHED, "Publish now")],
        initial=ContentStatus.DRAFT, label="Save as",
    )

    class Meta:
        model = Lesson
        fields = ["course", "module", "title", "description", "duration_minutes", "order", "is_free_preview"]
        widgets = {"description": forms.Textarea(attrs={"rows": 4})}

    field_order = [
        "course", "module", "title", "description", "source_type", "video_url", "video_file", "thumbnail",
        "duration_minutes", "duration_seconds", "order", "is_free_preview", "pdf_notes", "publish",
        "notify", "notify_batches", "notify_email",
    ]

    def limit_querysets(self):
        courses = self.courses_for_actor()
        self.fields["course"].queryset = courses
        self.fields["module"].queryset = CourseModule.objects.filter(course__in=courses).select_related("course")
        self.fields["module"].label_from_instance = module_choice_label
        self.fields["module"].widget.attrs["data-filter-by-course"] = "1"
        self._relax_numbers()
        self.fields["title"].label = "Lesson title"
        self.fields["description"].label = "Lesson description"
        self.setup_notify(default=Audience.ALL)

    def clean(self):
        cd = super().clean()
        if cd.get("module") and cd.get("course") and cd["module"].course_id != cd["course"].id:
            self.add_error("module", "This module does not belong to the selected course.")
        _validate_video_source(self, cd)
        return cd


def _validate_video_source(form, cd):
    st = cd.get("source_type")
    if not st:
        return
    probe = VideoAsset(source_type=st, video_url=cd.get("video_url") or "", video_file=cd.get("video_file"))
    if st == VideoSource.UPLOAD and not cd.get("video_file"):
        form.add_error("video_file", "Choose an MP4 file to upload.")
        return
    try:
        probe.clean()
    except forms.ValidationError as e:
        for field, errs in e.message_dict.items():
            form.add_error(field if field in form.fields else None, errs)


class VideoReplaceForm(NotifyMixin, BootstrapMixin, forms.Form):
    """Spec 4B: replace the active video with a new version."""

    title = forms.CharField(max_length=200, label="Video title")
    source_type = forms.ChoiceField(choices=VideoSource.choices, label="Video source")
    video_url = forms.URLField(required=False, label="Video URL")
    video_file = forms.FileField(required=False, label="Upload MP4", validators=[validate_video_upload])
    thumbnail = forms.ImageField(required=False, validators=[validate_image_upload], help_text="Leave empty to keep the current thumbnail")
    duration_seconds = forms.IntegerField(min_value=0, required=False, label="Duration (seconds)")
    status = forms.ChoiceField(choices=ContentStatus.choices, initial=ContentStatus.PUBLISHED)
    change_note = forms.CharField(max_length=255, required=False, help_text="e.g. 'Re-recorded with better audio'")
    delete_old_file = forms.BooleanField(
        required=False, label="Delete the previous uploaded MP4 from storage",
        help_text="History record is kept either way. Leave unchecked to allow rollback.",
    )

    def __init__(self, *args, lesson=None, actor=None, **kwargs):
        self.actor = actor
        super().__init__(*args, **kwargs)
        self.setup_notify(lesson.module.course if lesson else None, default=Audience.ALL)
        self._bootstrap()

    def clean(self):
        cd = super().clean()
        _validate_video_source(self, cd)
        return cd


class VideoAssetEditForm(StyledModelForm):
    """Edit metadata of an existing version without creating a new one."""

    class Meta:
        model = VideoAsset
        fields = ["title", "thumbnail", "duration_seconds", "status", "change_note"]


class ResourceForm(OptionalNumbersMixin, NotifyMixin, StyledModelForm):
    class Meta:
        model = LessonResource
        fields = ["lesson", "title", "resource_type", "file", "url", "description", "is_downloadable", "is_published", "order"]

    def limit_querysets(self):
        self.fields["lesson"].queryset = Lesson.objects.filter(
            module__course__in=self.courses_for_actor()
        ).select_related("module__course")
        self.fields["lesson"].label_from_instance = lesson_choice_label
        self._relax_numbers()
        self.setup_notify(self.instance.lesson.module.course if self.instance.pk else None,
                          default=Audience.SILENT if self.instance.pk else Audience.ALL)


# ----------------------------------------------------------------- batches & live
class BatchForm(StyledModelForm):
    class Meta:
        model = Batch
        fields = ["course", "name", "code", "trainer", "start_date", "end_date", "schedule", "capacity", "status", "is_open_for_enrollment"]
        widgets = {"start_date": D, "end_date": D}

    def limit_querysets(self):
        self.fields["course"].queryset = self.courses_for_actor()
        self.fields["trainer"].queryset = TrainerProfile.objects.select_related("user")


class LiveClassForm(StyledModelForm):
    notify_batch = forms.BooleanField(required=False, initial=True, label="Notify batch students (dashboard + email)")

    class Meta:
        model = LiveClass
        fields = ["batch", "title", "description", "meet_url", "start_at", "duration_minutes", "status", "recording_lesson"]
        widgets = {"start_at": DT, "description": forms.Textarea(attrs={"rows": 3})}

    def limit_querysets(self):
        courses = self.courses_for_actor()
        self.fields["batch"].queryset = Batch.objects.filter(course__in=courses).select_related("course")
        self.fields["recording_lesson"].queryset = Lesson.objects.filter(module__course__in=courses).select_related("module__course")
        self.fields["recording_lesson"].label_from_instance = lesson_choice_label
        self.fields["start_at"].input_formats = ["%Y-%m-%dT%H:%M"]


class BatchAddStudentForm(BootstrapMixin, forms.Form):
    student = forms.ModelChoiceField(queryset=User.objects.filter(role=Role.STUDENT, is_active=True))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._bootstrap()


# ----------------------------------------------------------------- enrollments
class EnrollmentForm(StyledModelForm):
    class Meta:
        model = Enrollment
        fields = ["student", "course", "batch", "status", "expires_at", "notes"]
        widgets = {"expires_at": DT}

    def limit_querysets(self):
        self.fields["student"].queryset = User.objects.filter(role=Role.STUDENT)
        self.fields["course"].queryset = Course.objects.all()
        self.fields["course"].required = True
        self.fields["batch"].queryset = Batch.objects.select_related("course")
        self.fields["expires_at"].input_formats = ["%Y-%m-%dT%H:%M"]
        self.fields["expires_at"].help_text = "Blank = use the course's access period (or lifetime)."
        if not self.instance.pk:
            self.initial.setdefault("status", EnrollmentStatus.ACTIVE)

    def clean(self):
        cd = super().clean()
        b, c = cd.get("batch"), cd.get("course")
        if b and c and b.course_id != c.id:
            self.add_error("batch", "Batch belongs to a different course.")
        if c and cd.get("student") and not self.instance.pk:
            if Enrollment.objects.filter(student=cd["student"], course=c).exists():
                self.add_error("course", "This student already has an enrollment for this course. Edit it instead.")
        return cd


class UpiReviewForm(BootstrapMixin, forms.Form):
    decision = forms.ChoiceField(choices=[("approve", "Approve & activate enrollment"), ("reject", "Reject")])
    note = forms.CharField(max_length=255, required=False, label="Note to student")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._bootstrap()


# ----------------------------------------------------------------- assignments
class AssignmentForm(StyledModelForm):
    class Meta:
        model = Assignment
        fields = ["course", "lesson", "title", "instructions", "attachment", "due_at", "max_marks", "is_published"]
        widgets = {"due_at": DT, "instructions": forms.Textarea(attrs={"rows": 6})}

    def limit_querysets(self):
        courses = self.courses_for_actor()
        self.fields["course"].queryset = courses
        self.fields["lesson"].queryset = Lesson.objects.filter(module__course__in=courses).select_related("module__course")
        self.fields["lesson"].label_from_instance = lesson_choice_label
        self.fields["due_at"].input_formats = ["%Y-%m-%dT%H:%M"]


class GradeForm(StyledModelForm):
    class Meta:
        model = AssignmentSubmission
        fields = ["status", "marks", "feedback"]
        widgets = {"feedback": forms.Textarea(attrs={"rows": 4})}


# ----------------------------------------------------------------- certificates & notices
class CertificateForm(StyledModelForm):
    class Meta:
        model = Certificate
        fields = ["student", "course", "grade", "remarks"]

    def limit_querysets(self):
        self.fields["student"].queryset = User.objects.filter(role=Role.STUDENT)
        self.fields["course"].queryset = Course.objects.all()
        self.fields["course"].required = True

    def clean(self):
        cd = super().clean()
        s, c = cd.get("student"), cd.get("course")
        if s and c and not self.instance.pk:
            if Certificate.objects.filter(student=s, course=c, is_revoked=False).exists():
                raise forms.ValidationError("This student already holds a valid certificate for this course.")
            if not Enrollment.objects.filter(student=s, course=c).exists():
                raise forms.ValidationError("The student is not enrolled in this course.")
        return cd


class AnnouncementForm(StyledModelForm):
    class Meta:
        model = Announcement
        fields = ["title", "body", "course", "batch", "is_published", "send_email"]
        widgets = {"body": forms.Textarea(attrs={"rows": 5})}

    def limit_querysets(self):
        courses = self.courses_for_actor()
        self.fields["course"].queryset = courses
        self.fields["batch"].queryset = Batch.objects.filter(course__in=courses)
        if self.actor and not self.actor.can_manage_all_content:
            self.fields["course"].required = True  # trainers cannot broadcast academy-wide


class SiteSettingsForm(StyledModelForm):
    class Meta:
        model = SiteSettings
        exclude = ["updated_at"]
        widgets = {
            "hero_subtitle": forms.Textarea(attrs={"rows": 2}),
            "about_text": forms.Textarea(attrs={"rows": 4}),
            "address": forms.Textarea(attrs={"rows": 2}),
            "upi_instructions": forms.Textarea(attrs={"rows": 3}),
        }


__all__ = [n for n in dir() if n.endswith("Form")] + ["ResourceType"]
