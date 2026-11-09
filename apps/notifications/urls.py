from django.urls import path

from . import views

app_name = "notifications"

urlpatterns = [
    path("", views.inbox, name="inbox"),
    path("<int:pk>/open/", views.open_notification, name="open"),
    path("read-all/", views.mark_all_read, name="read_all"),
]
