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
from .verification import email_token, send_verification_email, verification_required
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
        user = form.get_user()
        if user.totp_enabled:
            # Password OK; hold the login until the authenticator code is checked.
            from django.utils import timezone as tz

            self.request.session["2fa_pending"] = {
                "uid": user.pk, "ts": tz.now().timestamp(), "backend": user.backend, "next": self.get_success_url(),
            }
            return redirect("accounts:twofa_verify")
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
        if verification_required(user):
            try:
                send_verification_email(user, force=True)
                messages.success(request, f"Welcome! We've sent a confirmation link to {user.email}. "
                                          "Please verify your email to enroll in courses.")
            except Exception:
                messages.warning(request, "Your account is ready, but we couldn't send the confirmation email. "
                                          "Use 'Resend' on your dashboard to try again.")
        else:
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


def verify_email(request, uidb64, token):
    from django.utils.encoding import force_str
    from django.utils.http import urlsafe_base64_decode

    from .models import User

    try:
        user = User.objects.get(pk=force_str(urlsafe_base64_decode(uidb64)))
    except (ValueError, TypeError, OverflowError, User.DoesNotExist):
        user = None
    if user is not None and user.email_verified:
        messages.info(request, "Your email is already verified.")
    elif user is not None and email_token.check_token(user, token):
        user.email_verified = True
        user.save(update_fields=["email_verified"])
        audit.log(request, "email_verified", user)
        messages.success(request, "Thank you! Your email is verified. You can now enroll in courses.")
    else:
        messages.error(request, "This verification link is invalid or has expired. Log in and request a new one.")
        return redirect("accounts:verify_pending" if request.user.is_authenticated else "accounts:login")
    return redirect("portal:dashboard" if request.user.is_authenticated else "accounts:login")


@login_required
def verify_pending(request):
    nxt = request.GET.get("next", "")
    if not url_has_allowed_host_and_scheme(nxt, {request.get_host()}, request.is_secure()):
        nxt = ""
    if not verification_required(request.user):
        return redirect(nxt or "portal:dashboard")
    return render(request, "accounts/verify_pending.html", {"next": nxt})


@login_required
def resend_verification(request):
    if request.method != "POST":
        return redirect("accounts:verify_pending")
    if not verification_required(request.user):
        return redirect("portal:dashboard")
    try:
        sent = send_verification_email(request.user)
    except Exception:
        messages.error(request, "We couldn't send the email right now. Please try again later or contact the academy.")
        return redirect("accounts:verify_pending")
    if sent:
        messages.success(request, f"Confirmation link sent to {request.user.email}. Check your inbox (and spam folder).")
    else:
        messages.info(request, "A link was sent less than a minute ago. Please wait a moment before trying again.")
    return redirect("accounts:verify_pending")
