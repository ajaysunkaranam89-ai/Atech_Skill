"""Spec §11: Razorpay (order, signature, webhook) and manual UPI verification."""
import hashlib
import hmac
import json
from unittest import mock

from django.urls import reverse
from django.utils import timezone

from apps.courses.access import valid_enrollment
from apps.enrollments.models import Enrollment, EnrollmentStatus, Payment, PaymentStatus, UpiPaymentSubmission, UpiStatus

from .base import LMSTestCase, png


def sign(msg: bytes, secret: str):
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


class RazorpayTests(LMSTestCase):
    def setUp(self):
        super().setUp()
        self.course = self.make_course(price=4999)
        self.login(self.student)

    def create_order(self):
        fake = {"id": "order_TEST123", "amount": 499900, "currency": "INR"}
        with mock.patch("apps.enrollments.services._razorpay_request", return_value=fake) as m:
            r = self.client.post(reverse("enrollments:razorpay_order", args=[self.course.slug]))
        self.assertEqual(m.call_args[0][1]["amount"], 499900)  # amount from DB, not the browser
        return r

    def test_order_creation(self):
        r = self.create_order()
        data = r.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["order_id"], "order_TEST123")
        p = Payment.objects.get()
        self.assertEqual(p.status, PaymentStatus.CREATED)
        self.assertEqual(Enrollment.objects.get().status, EnrollmentStatus.PENDING)
        self.assertIsNone(valid_enrollment(self.student, self.course))

    def test_valid_signature_activates_enrollment(self):
        self.create_order()
        sig = sign(b"order_TEST123|pay_ABC", "test_secret")
        r = self.client.post(reverse("enrollments:razorpay_verify"), {
            "razorpay_order_id": "order_TEST123", "razorpay_payment_id": "pay_ABC", "razorpay_signature": sig})
        self.assertRedirects(r, reverse("portal:course_home", args=[self.course.slug]), fetch_redirect_response=False)
        p = Payment.objects.get()
        self.assertEqual(p.status, PaymentStatus.PAID)
        self.assertIsNotNone(valid_enrollment(self.student, self.course))

    def test_tampered_signature_rejected(self):
        self.create_order()
        self.client.post(reverse("enrollments:razorpay_verify"), {
            "razorpay_order_id": "order_TEST123", "razorpay_payment_id": "pay_ABC", "razorpay_signature": "deadbeef"})
        self.assertEqual(Payment.objects.get().status, PaymentStatus.FAILED)
        self.assertIsNone(valid_enrollment(self.student, self.course))

    def test_other_user_cannot_verify_my_order(self):
        self.create_order()
        self.login(self.outsider)
        sig = sign(b"order_TEST123|pay_ABC", "test_secret")
        self.client.post(reverse("enrollments:razorpay_verify"), {
            "razorpay_order_id": "order_TEST123", "razorpay_payment_id": "pay_ABC", "razorpay_signature": sig})
        self.assertIsNone(valid_enrollment(self.outsider, self.course))
        self.assertEqual(Payment.objects.get().status, PaymentStatus.CREATED)

    def webhook(self, body, secret="whsec_test"):
        raw = json.dumps(body).encode()
        return self.client.post(reverse("enrollments:razorpay_webhook"), raw, content_type="application/json",
                                HTTP_X_RAZORPAY_SIGNATURE=sign(raw, secret))

    def test_webhook_captured_activates_and_is_idempotent(self):
        self.create_order()
        self.client.logout()
        body = {"event": "payment.captured", "payload": {"payment": {"entity": {"id": "pay_W1", "order_id": "order_TEST123", "amount": 499900}}}}
        self.assertEqual(self.webhook(body).status_code, 200)
        self.assertEqual(self.webhook(body).status_code, 200)  # duplicate delivery
        self.assertEqual(Payment.objects.get().status, PaymentStatus.PAID)
        self.assertEqual(Enrollment.objects.filter(status=EnrollmentStatus.ACTIVE).count(), 1)

    def test_webhook_bad_signature_and_amount_mismatch(self):
        self.create_order()
        body = {"event": "payment.captured", "payload": {"payment": {"entity": {"id": "pay_W1", "order_id": "order_TEST123", "amount": 499900}}}}
        self.assertEqual(self.webhook(body, secret="wrong").status_code, 400)
        self.assertEqual(Payment.objects.get().status, PaymentStatus.CREATED)
        body["payload"]["payment"]["entity"]["amount"] = 100
        self.webhook(body)
        self.assertEqual(Payment.objects.get().status, PaymentStatus.FAILED)
        self.assertIsNone(valid_enrollment(self.student, self.course))

    def test_free_course_enrolls_directly_and_paid_does_not(self):
        free = self.make_course(price=0, title="Free Linux")
        self.client.post(reverse("enrollments:enroll_free", args=[free.slug]))
        self.assertIsNotNone(valid_enrollment(self.student, free))
        r = self.client.post(reverse("enrollments:enroll_free", args=[self.course.slug]))
        self.assertEqual(r.status_code, 400)
        self.assertIsNone(valid_enrollment(self.student, self.course))


class UpiTests(LMSTestCase):
    def setUp(self):
        super().setUp()
        self.course = self.make_course(price=4999)

    def submit(self, amount="4999", utr="UTR123456789"):
        self.login(self.student)
        return self.client.post(reverse("enrollments:upi_submit", args=[self.course.slug]), {
            "utr_number": utr, "payer_name": "Sam", "paid_on": timezone.localdate().isoformat(), "amount": amount,
            "screenshot": png()})

    def test_submission_pending_until_admin_approves(self):
        self.submit()
        sub = UpiPaymentSubmission.objects.get()
        self.assertEqual(sub.status, UpiStatus.PENDING)
        self.assertIsNone(valid_enrollment(self.student, self.course))
        # student cannot approve their own payment
        self.assertEqual(self.client.post(reverse("dashboard:upi_review", args=[sub.pk]), {"decision": "approve"}).status_code, 403)
        # course admin cannot either (payments are Super Admin only)
        self.login(self.course_admin)
        self.assertEqual(self.client.get(reverse("dashboard:upi_review", args=[sub.pk])).status_code, 403)
        self.login(self.owner)
        self.client.post(reverse("dashboard:upi_review", args=[sub.pk]), {"decision": "approve", "note": "ok"})
        sub.refresh_from_db()
        self.assertEqual(sub.status, UpiStatus.APPROVED)
        self.assertEqual(sub.payment.status, PaymentStatus.PAID)
        self.assertIsNotNone(valid_enrollment(self.student, self.course))

    def test_wrong_amount_and_duplicate_utr_rejected(self):
        r = self.submit(amount="100")
        self.assertContains(r, "exact course fee")
        self.submit()
        r = self.submit(utr="utr123456789")
        self.assertContains(r, "already been submitted")
        self.assertEqual(UpiPaymentSubmission.objects.count(), 1)

    def test_reject(self):
        self.submit()
        sub = UpiPaymentSubmission.objects.get()
        self.login(self.owner)
        self.client.post(reverse("dashboard:upi_review", args=[sub.pk]), {"decision": "reject", "note": "No credit found"})
        sub.refresh_from_db()
        self.assertEqual(sub.status, UpiStatus.REJECTED)
        self.assertIsNone(valid_enrollment(self.student, self.course))

    def test_screenshot_is_private(self):
        self.submit()
        sub = UpiPaymentSubmission.objects.get()
        self.assertEqual(self.client.get(reverse("portal:upi_screenshot", args=[sub.pk])).status_code, 200)
        self.login(self.outsider)
        self.assertEqual(self.client.get(reverse("portal:upi_screenshot", args=[sub.pk])).status_code, 403)
