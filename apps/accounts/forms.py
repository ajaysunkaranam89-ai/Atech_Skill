from django import forms
from django.contrib.auth import authenticate, password_validation
from django.contrib.auth.forms import AuthenticationForm

from .models import Role, StudentProfile, TrainerProfile, User


class BootstrapMixin:
    def _bootstrap(self):
        for name, field in self.fields.items():
            w = field.widget
            if isinstance(w, (forms.CheckboxInput,)):
                w.attrs.setdefault("class", "form-check-input")
            elif isinstance(w, (forms.Select, forms.SelectMultiple)):
                w.attrs.setdefault("class", "form-select")
            elif isinstance(w, forms.ClearableFileInput) or isinstance(w, forms.FileInput):
                w.attrs.setdefault("class", "form-control")
            else:
                w.attrs.setdefault("class", "form-control")


class LoginForm(BootstrapMixin, AuthenticationForm):
    username = forms.EmailField(label="Email", widget=forms.EmailInput(attrs={"autofocus": True, "autocomplete": "email"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._bootstrap()

    def clean(self):
        email = (self.cleaned_data.get("username") or "").lower()
        password = self.cleaned_data.get("password")
        if email and password:
            self.user_cache = authenticate(self.request, username=email, password=password)
            if self.user_cache is None:
                raise forms.ValidationError("Invalid email or password.", code="invalid_login")
            self.confirm_login_allowed(self.user_cache)
        return self.cleaned_data


class SignupForm(BootstrapMixin, forms.ModelForm):
    password1 = forms.CharField(label="Password", widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))
    password2 = forms.CharField(label="Confirm password", widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))
    accept_terms = forms.BooleanField(
        required=True, error_messages={"required": "Please accept the Terms & Conditions and Privacy Policy."}
    )

    class Meta:
        model = User
        fields = ["first_name", "last_name", "email", "phone"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["first_name"].required = True
        self._bootstrap()

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email

    def clean(self):
        cd = super().clean()
        if cd.get("password1") != cd.get("password2"):
            self.add_error("password2", "Passwords do not match.")
        elif cd.get("password1"):
            tmp = User(email=cd.get("email", ""), first_name=cd.get("first_name", ""))
            try:
                password_validation.validate_password(cd["password1"], tmp)
            except forms.ValidationError as e:
                self.add_error("password1", e)
        return cd

    def save(self, commit=True):
        user = super().save(commit=False)
        user.role = Role.STUDENT
        user.email_verified = False
        user.set_password(self.cleaned_data["password1"])
        if commit:
            user.save()
            StudentProfile.objects.get_or_create(user=user)
        return user


class ProfileForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = User
        fields = ["first_name", "last_name", "phone"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._bootstrap()


class StudentProfileForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = StudentProfile
        fields = ["city", "qualification", "current_role", "linkedin_url", "photo"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._bootstrap()


# ------------------------------------------------------------- admin-side forms
class AdminUserForm(BootstrapMixin, forms.ModelForm):
    password = forms.CharField(
        required=False, widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        help_text="Leave blank to keep the current password. Required for new users.",
    )

    class Meta:
        model = User
        fields = ["first_name", "last_name", "email", "phone", "role", "is_active", "email_verified"]

    def __init__(self, *args, actor=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.actor = actor
        # Only Super Admins can create/modify administrator accounts.
        if actor is None or not actor.is_super_admin:
            self.fields["role"].choices = [(Role.STUDENT, "Student")]
        self._bootstrap()

    def clean_email(self):
        return self.cleaned_data["email"].lower()

    def clean(self):
        cd = super().clean()
        if not self.instance.pk and not cd.get("password"):
            self.add_error("password", "Password is required for new users.")
        if cd.get("password"):
            try:
                password_validation.validate_password(cd["password"], self.instance)
            except forms.ValidationError as e:
                self.add_error("password", e)
        if self.instance.pk and self.actor and self.instance.pk == self.actor.pk:
            if cd.get("role") != self.instance.role or cd.get("is_active") is False:
                raise forms.ValidationError("You cannot change your own role or deactivate yourself.")
        return cd

    def save(self, commit=True):
        user = super().save(commit=False)
        if self.cleaned_data.get("password"):
            user.set_password(self.cleaned_data["password"])
        if commit:
            user.save()
            if user.role == Role.STUDENT:
                StudentProfile.objects.get_or_create(user=user)
            if user.role == Role.TRAINER:
                TrainerProfile.objects.get_or_create(user=user)
        return user


class TrainerProfileForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = TrainerProfile
        fields = ["designation", "bio", "expertise", "experience_years", "photo", "linkedin_url", "is_public"]
        widgets = {"bio": forms.Textarea(attrs={"rows": 4})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._bootstrap()
