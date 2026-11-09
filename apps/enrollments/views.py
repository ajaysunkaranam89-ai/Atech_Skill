import logging

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.accounts.forms import BootstrapMixin
from apps.core import audit
from apps.core.models import SiteSettings
from apps.courses.access import valid_enrollment
from apps.courses.models import Batch, BatchStatus, Course, CourseStatus

from . import services
from .models import Payment, PaymentStatus, UpiPaymentSubmission, UpiStatus

logger = logging.getLogger(__name__)


class UpiForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = UpiPaymentSubmission
        fields = ["utr_number", "payer_name", "payer_upi_id", "paid_on", "amount", "screenshot"]
        widgets = {"paid_on": forms.DateInput(attrs={"type": "date"})}
        help_texts = {"utr_number": "12-digit UTR / UPI reference number from your payment app"}

    def __init__(self, *args, expected_amount=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.expected_amount = expected_amount
        self.fields["screenshot"].required = True
        self._bootstrap()

    def clean_utr_number(self):
        utr = self.cleaned_data["utr_number"].strip().upper()
        if not utr.isalnum() or len(utr) < 8:
            raise forms.ValidationError("Enter a valid UTR / transaction reference.")
        if UpiPaymentSubmission.objects.filter(utr_number=utr).exists():
            raise forms.ValidationError("This UTR has already been submitted.")
        return utr

    def clean_paid_on(self):
        d = self.cleaned_data["paid_on"]
        if d > timezone.localdate():
            raise forms.ValidationError("Payment date cannot be in the future.")
        return d

    def clean_amount(self):
        a = self.cleaned_data["amount"]
        if self.expected_amount is not None and a != self.expected_amount:
            raise forms.ValidationError(f"Please pay and enter the exact course fee: ₹{self.expected_amount}.")
        return a


def _open_batches(course):
    return course.batches.filter(is_open_for_enrollment=True, status__in=[BatchStatus.UPCOMING, BatchStatus.ONGOING]).order_by("start_date")


def _selected_batch(request, course):
    bid = request.POST.get("batch") or request.GET.get("batch")
    if not bid:
        return None
    try:
        return _open_batches(course).get(pk=int(bid))
    except (ValueError, Batch.DoesNotExist):
        return None


@login_required
def checkout(request, slug):
    course = get_object_or_404(Course, slug=slug, status=CourseStatus.PUBLISHED)
    if valid_enrollment(request.user, course):
        messages.info(request, "You are already enrolled in this course.")
        return redirect("portal:course_home", slug)
    site = SiteSettings.load()
    batch = _selected_batch(request, course)
    upi_form = UpiForm(expected_amount=course.effective_price, initial={"amount": course.effective_price, "payer_name": request.user.get_full_name()})
    pending_upi = UpiPaymentSubmission.objects.filter(student=request.user, course=course, status=UpiStatus.PENDING).first()
    return render(request, "enrollments/checkout.html", {
        "course": course, "academy": site, "batches": _open_batches(course), "batch": batch, "upi_form": upi_form,
        "razorpay_ready": site.razorpay_enabled and services.razorpay_configured(), "pending_upi": pending_upi,
    })


@login_required
@require_POST
def enroll_free(request, slug):
    course = get_object_or_404(Course, slug=slug, status=CourseStatus.PUBLISHED)
    if not course.is_free:
        return HttpResponseBadRequest("This course is not free")
    services.enroll_free(request.user, course, batch=_selected_batch(request, course))
    audit.log(request, "enroll_free", course)
    messages.success(request, f"You are enrolled in {course.title}.")
    return redirect("portal:course_home", slug)


@login_required
@require_POST
def razorpay_create_order(request, slug):
    course = get_object_or_404(Course, slug=slug, status=CourseStatus.PUBLISHED)
    site = SiteSettings.load()
    if not site.razorpay_enabled:
        return JsonResponse({"ok": False, "error": "Online payment is disabled."}, status=400)
    if valid_enrollment(request.user, course):
        return JsonResponse({"ok": False, "error": "Already enrolled."}, status=400)
    try:
        payment = services.create_razorpay_order(request.user, course, batch=_selected_batch(request, course))
    except services.PaymentError as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)
    return JsonResponse({
        "ok": True,
        "key": settings.RAZORPAY_KEY_ID,
        "order_id": payment.razorpay_order_id,
        "amount": payment.amount_paise,
        "currency": payment.currency,
        "name": site.site_name,
        "description": course.title,
        "prefill": {"name": request.user.get_full_name(), "email": request.user.email, "contact": request.user.phone},
    })


@login_required
@require_POST
def razorpay_verify(request):
    order_id = request.POST.get("razorpay_order_id", "")
    payment_id = request.POST.get("razorpay_payment_id", "")
    signature = request.POST.get("razorpay_signature", "")
    payment = Payment.objects.filter(razorpay_order_id=order_id, student=request.user).select_related("course").first()
    if not payment:
        messages.error(request, "Payment not found.")
        return redirect("portal:payments")
    if not services.verify_checkout_signature(order_id, payment_id, signature):
        payment.status = PaymentStatus.FAILED
        payment.failure_reason = "Signature verification failed"
        payment.save(update_fields=["status", "failure_reason", "updated_at"])
        audit.log(request, "payment_signature_invalid", payment)
        messages.error(request, "Payment verification failed. If money was debited, contact the academy with your payment ID.")
        return redirect("portal:payments")
    services.mark_payment_paid(payment, payment_id, signature)
    audit.log(request, "payment_verified", payment)
    messages.success(request, "Payment successful! Your enrollment is active.")
    return redirect("portal:course_home", payment.course.slug) if payment.course else redirect("portal:dashboard")


@csrf_exempt  # authenticated by HMAC signature instead
@require_POST
def razorpay_webhook(request):
    try:
        services.handle_webhook(request.body, request.headers.get("X-Razorpay-Signature", ""))
    except services.PaymentError:
        logger.warning("Rejected Razorpay webhook with invalid signature")
        return HttpResponse(status=400)
    except Exception:
        logger.exception("Error processing Razorpay webhook")
        return HttpResponse(status=500)
    return HttpResponse("ok")


@login_required
@require_POST
def upi_submit(request, slug):
    course = get_object_or_404(Course, slug=slug, status=CourseStatus.PUBLISHED)
    site = SiteSettings.load()
    if not site.upi_enabled:
        messages.error(request, "UPI payments are currently disabled.")
        return redirect("enrollments:checkout", slug)
    form = UpiForm(request.POST, request.FILES, expected_amount=course.effective_price)
    if form.is_valid():
        sub = form.save(commit=False)
        sub.student = request.user
        sub.course = course
        sub.batch = _selected_batch(request, course)
        sub.save()
        services.get_or_create_pending(request.user, course, sub.batch)
        audit.log(request, "upi_submit", sub, {"utr": sub.utr_number, "amount": str(sub.amount)})
        messages.success(request, "Payment details submitted. Your enrollment will be activated after verification.")
        return redirect("portal:payments")
    return render(request, "enrollments/checkout.html", {
        "course": course, "academy": site, "batches": _open_batches(course), "batch": _selected_batch(request, course),
        "upi_form": form, "razorpay_ready": site.razorpay_enabled and services.razorpay_configured(), "show_upi": True,
    })
