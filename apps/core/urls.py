from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("", views.home, name="home"),
    path("courses/", views.course_list, name="course_list"),
    path("courses/<slug:slug>/", views.course_detail, name="course_detail"),
    path("privacy-policy/", views.policy_page, {"kind": "privacy"}, name="privacy"),
    path("terms-and-conditions/", views.policy_page, {"kind": "terms"}, name="terms"),
    path("refund-policy/", views.policy_page, {"kind": "refund"}, name="refund"),
    path("contact/", views.contact, name="contact"),
    path("enquiry/", views.enquiry_submit, name="enquiry"),
    path("about/", views.about, name="about"),
    path("trainers/", views.trainers, name="trainers"),
    path("trainers/<int:pk>/", views.trainer_detail, name="trainer_detail"),
    path("certificates/verify/<str:number>/", views.certificate_verify, name="certificate_verify"),
    path("healthz/", views.healthz, name="healthz"),
]
