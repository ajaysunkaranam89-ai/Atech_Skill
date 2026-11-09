from django.contrib.auth import views as auth_views
from django.urls import path, reverse_lazy

from . import twofa, views

app_name = "accounts"

urlpatterns = [
    path("login/", views.LoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("signup/", views.signup, name="signup"),
    path("after-login/", views.after_login, name="after_login"),
    path("profile/", views.profile, name="profile"),
    path("verify-email/", views.verify_pending, name="verify_pending"),
    path("2fa/verify/", twofa.verify, name="twofa_verify"),
    path("2fa/", twofa.setup, name="twofa_setup"),
    path("2fa/recovery-codes/", twofa.regenerate_codes, name="twofa_codes"),
    path("2fa/reset/", twofa.reset_own, name="twofa_reset"),
    path("verify-email/resend/", views.resend_verification, name="resend_verification"),
    path("verify-email/<uidb64>/<token>/", views.verify_email, name="verify_email"),
    path("password/change/", views.PasswordChangeView.as_view(), name="password_change"),
    path(
        "password/reset/",
        auth_views.PasswordResetView.as_view(
            template_name="accounts/password_reset.html",
            email_template_name="accounts/password_reset_email.txt",
            success_url=reverse_lazy("accounts:password_reset_done"),
        ),
        name="password_reset",
    ),
    path(
        "password/reset/done/",
        auth_views.PasswordResetDoneView.as_view(template_name="accounts/password_reset_done.html"),
        name="password_reset_done",
    ),
    path(
        "reset/<uidb64>/<token>/",
        auth_views.PasswordResetConfirmView.as_view(
            template_name="accounts/password_reset_confirm.html",
            success_url=reverse_lazy("accounts:login"),
        ),
        name="password_reset_confirm",
    ),
]
