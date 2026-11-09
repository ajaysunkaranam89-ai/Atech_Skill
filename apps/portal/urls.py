from django.urls import path

from . import views

app_name = "portal"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("course/<slug:slug>/", views.course_home, name="course_home"),
    path("course/<slug:slug>/lesson/<int:pk>/", views.lesson, name="lesson"),
    path("lesson/<int:pk>/complete/", views.lesson_complete, name="lesson_complete"),
    path("video/<int:pk>/", views.video_stream, name="video_stream"),
    path("material/<int:pk>/", views.resource_download, name="resource_download"),
    path("assignments/", views.assignments, name="assignments"),
    path("assignments/<int:pk>/", views.assignment_detail, name="assignment_detail"),
    path("assignments/<int:pk>/brief/", views.assignment_brief, name="assignment_brief"),
    path("submissions/<int:pk>/file/", views.submission_file, name="submission_file"),
    path("upi/<int:pk>/screenshot/", views.upi_screenshot, name="upi_screenshot"),
    path("certificates/", views.certificates, name="certificates"),
    path("certificates/<str:number>/", views.certificate, name="certificate"),
    path("payments/", views.payments, name="payments"),
]
