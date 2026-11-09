from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.debug import sensitive_post_parameters

from apps.core import audit

from . import ratelimit
from .forms import LoginForm, ProfileForm, SignupForm, StudentProfileForm
from .models import StudentProfile


class LoginView(auth_views.LoginView):
    template_name = "accounts/login.html"
    authentication_form = LoginForm
    redirect_authenticated_user = True

    def post(self, request, *args, **kwargs):
        email = request.POST.get("username", "")
        if ratelimit.is_locked(request, email):
            form = self.get_form()
            form.errors.clear()
            form.add_error(None, "Too many failed login attempts. Please try again in 15 minutes.")
            audit.log(request, "login_locked", object_type="user", object_repr=email)
            return self.render_to_response(self.get_context_data(form=form), status=429)
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        ratelimit.reset(self.request, form.cleaned_data.get("username"))
        response = super().form_valid(form)  # rotates the session key
        audit.log(self.request, "login", self.request.user)
        return response

    def form_invalid(self, form):
        email = self.request.POST.get("username", "")
        ratelimit.register_failure(self.request, email)
        audit.log(self.request, "login_failed", object_type="user", object_repr=email)
        return super().form_invalid(form)


@sensitive_post_parameters("password1", "password2")
def signup(request):
    if request.user.is_authenticated:
        return redirect("accounts:after_login")
    form = SignupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        audit.log(request, "signup", user)
        messages.success(request, "Welcome to A TechSkills Academy! Your account is ready.")
        nxt = request.GET.get("next")
        if nxt and url_has_allowed_host_and_scheme(nxt, {request.get_host()}, request.is_secure()):
            return redirect(nxt)
        return redirect("portal:dashboard")
    return render(request, "accounts/signup.html", {"form": form})


@login_required
def after_login(request):
    nxt = request.GET.get("next")
    if nxt and url_has_allowed_host_and_scheme(nxt, {request.get_host()}, request.is_secure()):
        return redirect(nxt)
    if request.user.is_academy_staff:
        return redirect("dashboard:overview")
    return redirect("portal:dashboard")


@login_required
def profile(request):
    sp = None
    if request.user.is_student:
        sp, _ = StudentProfile.objects.get_or_create(user=request.user)
    form = ProfileForm(request.POST or None, instance=request.user, prefix="u")
    sform = StudentProfileForm(request.POST or None, request.FILES or None, instance=sp, prefix="s") if sp else None
    if request.method == "POST" and form.is_valid() and (sform is None or sform.is_valid()):
        form.save()
        if sform:
            sform.save()
        messages.success(request, "Profile updated.")
        return redirect("accounts:profile")
    return render(request, "accounts/profile.html", {"form": form, "sform": sform})


class PasswordChangeView(auth_views.PasswordChangeView):
    template_name = "accounts/password_change.html"
    success_url = reverse_lazy("accounts:profile")

    def form_valid(self, form):
        messages.success(self.request, "Password changed.")
        audit.log(self.request, "password_change", self.request.user)
        return super().form_valid(form)
