"""Enrollment & payment logic.

Rules enforced here:
* An enrollment becomes ACTIVE only after a verified Razorpay payment
  (signature or webhook), an administrator-approved UPI submission, an
  administrator's manual enrollment, or for a free course.
* The amount is always taken from the database, never from the browser.
* Activation is idempotent (callback + webhook may both arrive).
"""
import base64
import hashlib
import hmac
import json
import logging
import urllib.error
import urllib.request

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.notifications.models import NotificationKind
from apps.notifications.services import notify_users

from .models import (
    Enrollment,
    EnrollmentSource,
    EnrollmentStatus,
    Payment,
    PaymentGateway,
    PaymentStatus,
    UpiPaymentSubmission,
    UpiStatus,
)

logger = logging.getLogger(__name__)


class PaymentError(Exception):
    pass


def razorpay_configured():
    return bool(settings.RAZORPAY_KEY_ID and settings.RAZORPAY_KEY_SECRET)


# ------------------------------------------------------------------ enrollment
def get_or_create_pending(student, course, batch=None):
    enr, _ = Enrollment.objects.get_or_create(
        student=student, course=course, defaults={"status": EnrollmentStatus.PENDING, "batch": batch}
    )
    return enr


def activate_enrollment(student, course, source, batch=None, notify=True):
    with transaction.atomic():
        enr = Enrollment.objects.select_for_update().filter(student=student, course=course).first()
        if enr is None:
            enr = Enrollment.objects.create(student=student, course=course, status=EnrollmentStatus.PENDING)
            enr = Enrollment.objects.select_for_update().get(pk=enr.pk)
        was_valid = enr.is_valid
        enr.activate(source, batch=batch)
    full = getattr(enr, "_batch_was_full", None)
    if full is not None:
        from django.contrib.auth import get_user_model

        admins = get_user_model().objects.filter(is_active=True).filter(role="super_admin")
        notify_users(
            admins, NotificationKind.ENROLLMENT, f"Batch full: assign {student.email} manually",
            f"{student.get_full_name() or student.email} enrolled in {course.title} but batch {full.code} was full. "
            "Their course access is active; please assign another batch or raise the capacity.",
            link=f"/dashboard/enrollments/{enr.pk}/edit/", dedupe_key=f"batchfull:{enr.pk}:{full.pk}",
        )
    if notify and not was_valid:
        notify_users(
            [student], NotificationKind.ENROLLMENT, f"Enrollment confirmed: {course.title}",
            "Your enrollment is active. Start learning from your dashboard.",
            link=f"/learn/course/{course.slug}/", dedupe_key=f"enrolled:{enr.pk}:{enr.activated_at:%Y%m%d%H%M%S}",
        )
    return enr


def enroll_free(student, course, batch=None):
    if not course.is_free:
        raise PaymentError("This course is not free.")
    return activate_enrollment(student, course, EnrollmentSource.FREE, batch=batch)


# ------------------------------------------------------------------ razorpay
def _razorpay_request(path, payload):
    auth = base64.b64encode(f"{settings.RAZORPAY_KEY_ID}:{settings.RAZORPAY_KEY_SECRET}".encode()).decode()
    req = urllib.request.Request(
        f"https://api.razorpay.com/v1/{path}",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Basic {auth}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="ignore")
        logger.error("Razorpay error %s: %s", e.code, body)
        raise PaymentError("Payment gateway rejected the request.") from e
    except urllib.error.URLError as e:
        raise PaymentError("Could not reach the payment gateway. Please try again.") from e


def create_razorpay_order(student, course, batch=None):
    """Create a Payment row + Razorpay order. Amount comes from the DB."""
    if not razorpay_configured():
        raise PaymentError("Online payment is not configured.")
    amount = course.effective_price
    if amount <= 0:
        raise PaymentError("This course is free; no payment needed.")
    enr = get_or_create_pending(student, course, batch)
    payment = Payment.objects.create(
        student=student, course=course, enrollment=enr, batch=batch, gateway=PaymentGateway.RAZORPAY,
        amount=amount, currency=settings.PAYMENT_CURRENCY,
    )
    order = _razorpay_request(
        "orders",
        {
            "amount": payment.amount_paise,
            "currency": payment.currency,
            "receipt": f"pay_{payment.pk}",
            "notes": {"payment_id": str(payment.pk), "course": course.slug, "student": student.email},
        },
    )
    payment.razorpay_order_id = order["id"]
    payment.raw_response = {"order": order}
    payment.save(update_fields=["razorpay_order_id", "raw_response", "updated_at"])
    return payment


def verify_checkout_signature(order_id, payment_id, signature):
    msg = f"{order_id}|{payment_id}".encode()
    expected = hmac.new(settings.RAZORPAY_KEY_SECRET.encode(), msg, hashlib.sha256).hexdigest()
    return bool(signature) and hmac.compare_digest(expected, signature)


def verify_webhook_signature(body: bytes, signature):
    if not settings.RAZORPAY_WEBHOOK_SECRET or not signature:
        return False
    expected = hmac.new(settings.RAZORPAY_WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def mark_payment_paid(payment, razorpay_payment_id="", signature="", raw=None, amount_paise=None):
    """Idempotently mark a Razorpay payment paid and activate the enrollment."""
    with transaction.atomic():
        payment = Payment.objects.select_for_update().get(pk=payment.pk)
        if amount_paise is not None and int(amount_paise) != payment.amount_paise:
            payment.status = PaymentStatus.FAILED
            payment.failure_reason = f"Amount mismatch: got {amount_paise} paise"
            payment.save()
            logger.error("Razorpay amount mismatch on payment %s", payment.pk)
            return payment
        if payment.status != PaymentStatus.PAID:
            payment.status = PaymentStatus.PAID
            payment.paid_at = timezone.now()
            payment.razorpay_payment_id = razorpay_payment_id or payment.razorpay_payment_id
            payment.razorpay_signature = signature or payment.razorpay_signature
            if raw:
                payment.raw_response = {**(payment.raw_response or {}), **raw}
            payment.save()
    if payment.course_id:
        enr = activate_enrollment(payment.student, payment.course, EnrollmentSource.RAZORPAY, batch=payment.batch)
        if payment.enrollment_id != enr.pk:
            Payment.objects.filter(pk=payment.pk).update(enrollment=enr)
    if payment.status == PaymentStatus.PAID:
        _issue_invoice_safely(payment)
    return payment


def handle_webhook(body: bytes, signature):
    if not verify_webhook_signature(body, signature):
        raise PaymentError("Invalid webhook signature")
    event = json.loads(body.decode())
    etype = event.get("event", "")
    entity = (event.get("payload", {}).get("payment") or {}).get("entity") or {}
    order_id = entity.get("order_id") or ((event.get("payload", {}).get("order") or {}).get("entity") or {}).get("id")
    if not order_id:
        return None
    payment = Payment.objects.filter(razorpay_order_id=order_id).first()
    if payment is None:
        logger.warning("Webhook for unknown order %s", order_id)
        return None
    if etype in ("payment.captured", "order.paid"):
        return mark_payment_paid(payment, entity.get("id", ""), raw={"webhook": event}, amount_paise=entity.get("amount"))
    if etype == "payment.failed" and payment.status != PaymentStatus.PAID:
        payment.status = PaymentStatus.FAILED
        payment.failure_reason = (entity.get("error_description") or "")[:255]
        payment.save(update_fields=["status", "failure_reason", "updated_at"])
    return payment


def _issue_invoice_safely(payment):
    """Create the invoice and tell the student. Never breaks the payment flow."""
    from .invoicing import issue_invoice

    try:
        payment.refresh_from_db()
        inv = issue_invoice(payment)
    except Exception:
        logger.exception("Could not issue invoice for payment %s", payment.pk)
        return None
    if inv is not None:
        notify_users(
            [payment.student], NotificationKind.PAYMENT, f"Payment received: ₹{inv.total}",
            f"Thank you! {inv.title} {inv.number} for {payment.course_title} is ready to download.",
            link="/learn/payments/", dedupe_key=f"invoice:{inv.pk}",
        )
    return inv


# ------------------------------------------------------------------ UPI (manual verification)
def approve_upi(submission: UpiPaymentSubmission, reviewer, note=""):
    with transaction.atomic():
        sub = UpiPaymentSubmission.objects.select_for_update().get(pk=submission.pk)
        if sub.status == UpiStatus.APPROVED:
            return sub
        payment = Payment.objects.create(
            student=sub.student, course=sub.course, batch=sub.batch, gateway=PaymentGateway.UPI,
            amount=sub.amount, status=PaymentStatus.PAID, paid_at=timezone.now(), upi_reference=sub.utr_number,
        )
        sub.status = UpiStatus.APPROVED
        sub.reviewed_by = reviewer
        sub.reviewed_at = timezone.now()
        sub.review_note = note
        sub.payment = payment
        sub.save()
    if sub.course_id:
        enr = activate_enrollment(sub.student, sub.course, EnrollmentSource.UPI, batch=sub.batch)
        Payment.objects.filter(pk=payment.pk).update(enrollment=enr)
    _issue_invoice_safely(payment)
    return sub


def reject_upi(submission, reviewer, note=""):
    submission.status = UpiStatus.REJECTED
    submission.reviewed_by = reviewer
    submission.reviewed_at = timezone.now()
    submission.review_note = note
    submission.save()
    notify_users(
        [submission.student], NotificationKind.PAYMENT, "UPI payment could not be verified",
        f"UTR {submission.utr_number}: {note or 'Please contact the academy.'}",
        link="/learn/payments/", dedupe_key=f"upi-reject:{submission.pk}",
    )
    return submission
