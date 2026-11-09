from django.urls import path

from . import views

app_name = "enrollments"

urlpatterns = [
    path("checkout/<slug:slug>/", views.checkout, name="checkout"),
    path("checkout/<slug:slug>/free/", views.enroll_free, name="enroll_free"),
    path("checkout/<slug:slug>/razorpay/order/", views.razorpay_create_order, name="razorpay_order"),
    path("checkout/<slug:slug>/upi/", views.upi_submit, name="upi_submit"),
    path("razorpay/verify/", views.razorpay_verify, name="razorpay_verify"),
    path("razorpay/webhook/", views.razorpay_webhook, name="razorpay_webhook"),
]
