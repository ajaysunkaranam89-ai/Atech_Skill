"""Item 7 (automatic certificates + PDF) and item 8 (invoices / receipts + GST)."""
import hashlib
import hmac
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import StudentProfile
from apps.core.models import SiteSettings
from apps.courses.models import Certificate
from apps.enrollments.invoicing import amount_in_words, financial_year, split_tax
from apps.enrollments.models import EnrollmentStatus, Invoice, Payment, PaymentGateway, PaymentStatus, UpiPaymentSubmission
from apps.notifications.models import Notification

from .base import LMSTestCase, png


class AutoCertificateTests(LMSTestCase):
    def setUp(self):
        super().setUp()
        self.course = self.make_course()
        m = self.make_module(self.course)
        self.l1 = self.make_lesson(m, title="One")
        self.l2 = self.make_lesson(m, title="Two")
        self.enrollment = self.enroll(self.student, self.course)
        self.login(self.student)

    def complete(self, lesson):
        return self.client.post(reverse("portal:lesson_complete", args=[lesson.pk]), {"completed": "1"},
                                HTTP_X_REQUESTED_WITH="XMLHttpRequest").json()

    def test_issued_once_at_100_percent(self):
        self.assertIsNone(self.complete(self.l1)["certificate"])
        self.assertFalse(Certificate.objects.exists())
        data = self.complete(self.l2)
        self.assertTrue(data["certificate"].startswith("ATSA-"))
        cert = Certificate.objects.get()
        self.assertEqual(cert.student, self.student)
        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.status, EnrollmentStatus.COMPLETED)
        self.assertTrue(Notification.objects.filter(user=self.student, kind="certificate").exists())
        # toggling a lesson off and on again never issues a duplicate
        self.client.post(reverse("portal:lesson_complete", args=[self.l2.pk]), {"completed": "0"})
        self.assertIsNone(self.complete(self.l2)["certificate"])
        self.assertEqual(Certificate.objects.count(), 1)
        # completed students keep access
        self.assertEqual(self.client.get(self.l1.get_absolute_url()).status_code, 200)

    def test_not_issued_when_disabled(self):
        self.course.auto_issue_certificate = False
        self.course.save()
        self.complete(self.l1)
        self.complete(self.l2)
        self.assertFalse(Certificate.objects.exists())

    def test_api_also_issues(self):
        self.complete(self.l1)
        r = self.client.post(reverse("api_lesson_progress", args=[self.l2.pk]), {"completed": True}, content_type="application/json")
        self.assertIsNotNone(r.json()["certificate"])

    def test_certificate_pdf_download_and_access(self):
        self.complete(self.l1)
        number = self.complete(self.l2)["certificate"]
        r = self.client.get(reverse("portal:certificate_pdf", args=[number]))
        self.assertEqual(r["Content-Type"], "application/pdf")
        self.assertTrue(r.content.startswith(b"%PDF"))
        self.assertIn("attachment", r["Content-Disposition"])
        self.login(self.outsider)
        self.assertEqual(self.client.get(reverse("portal:certificate_pdf", args=[number])).status_code, 403)
        self.login(self.course_admin)
        self.assertEqual(self.client.get(reverse("portal:certificate_pdf", args=[number])).status_code, 200)


class TaxMathTests(LMSTestCase):
    def test_inclusive_gst_split(self):
        self.assertEqual(split_tax(1180, 18, "29", "29", True),
                         (Decimal("1000.00"), Decimal("90.00"), Decimal("90.00"), Decimal("0.00")))
        self.assertEqual(split_tax(1180, 18, "29", "36", True),
                         (Decimal("1000.00"), Decimal("0.00"), Decimal("0.00"), Decimal("180.00")))
        taxable, c, s, i = split_tax(4999, 18, "29", "", True)  # unknown buyer state -> intra-state
        self.assertEqual(taxable + c + s + i, Decimal("4999.00"))
        self.assertEqual(split_tax(4999, 18, "", "", False)[0], Decimal("4999.00"))

    def test_amount_in_words(self):
        self.assertEqual(amount_in_words(Decimal("125000.50")), "One Lakh Twenty Five Thousand Rupees and Fifty Paise Only")
        self.assertEqual(amount_in_words(1999), "One Thousand Nine Hundred Ninety Nine Rupees Only")
        self.assertEqual(amount_in_words(0), "Zero Rupees Only")

    def test_financial_year(self):
        tz = timezone.get_current_timezone()
        self.assertEqual(financial_year(timezone.datetime(2026, 3, 31, 12, tzinfo=tz)), "2025-26")
        self.assertEqual(financial_year(timezone.datetime(2026, 4, 1, 12, tzinfo=tz)), "2026-27")


class InvoiceTests(LMSTestCase):
    def setUp(self):
        super().setUp()
        self.course = self.make_course(price=1180, title="CCNA")
        s = SiteSettings.load()
        s.legal_business_name = "A TechSkills Academy"
        s.gstin = "29ABCDE1234F1Z5"
        s.business_state = "29"
        s.invoice_mode = "gst"
        s.save()

    def pay_razorpay(self, user):
        self.login(user)
        with mock.patch("apps.enrollments.services._razorpay_request", return_value={"id": f"order_{user.pk}", "amount": 118000}):
            self.client.post(reverse("enrollments:razorpay_order", args=[self.course.slug]))
        sig = hmac.new(b"test_secret", f"order_{user.pk}|pay_{user.pk}".encode(), hashlib.sha256).hexdigest()
        self.client.post(reverse("enrollments:razorpay_verify"), {
            "razorpay_order_id": f"order_{user.pk}", "razorpay_payment_id": f"pay_{user.pk}", "razorpay_signature": sig})
        return Payment.objects.get(student=user)

    def test_tax_invoice_on_razorpay_payment_sequential_and_idempotent(self):
        p1 = self.pay_razorpay(self.student)
        inv = p1.invoice
        fy = financial_year()
        self.assertEqual(inv.number, f"ATSA/{fy}/0001")
        self.assertTrue(inv.is_tax_invoice)
        self.assertEqual((inv.taxable_value, inv.cgst, inv.sgst, inv.igst), (Decimal("1000.00"), Decimal("90.00"), Decimal("90.00"), Decimal("0.00")))
        self.assertEqual(inv.payment_reference, f"pay_{self.student.pk}")
        self.assertTrue(Notification.objects.filter(user=self.student, kind="payment", title__startswith="Payment received").exists())
        # webhook re-delivery does not create another invoice
        from apps.enrollments.services import mark_payment_paid

        mark_payment_paid(p1, "pay_x")
        self.assertEqual(Invoice.objects.count(), 1)
        # buyer in another state -> IGST, next number
        StudentProfile.objects.update_or_create(user=self.outsider, defaults={"state": "36"})
        p2 = self.pay_razorpay(self.outsider)
        self.assertEqual(p2.invoice.number, f"ATSA/{fy}/0002")
        self.assertEqual(p2.invoice.igst, Decimal("180.00"))

    def test_invoice_pdf_access(self):
        p = self.pay_razorpay(self.student)
        r = self.client.get(reverse("portal:invoice_pdf", args=[p.pk]))
        self.assertTrue(r.content.startswith(b"%PDF"))
        self.assertContains(self.client.get(reverse("portal:payments")), p.invoice.number)
        self.login(self.outsider)
        self.assertEqual(self.client.get(reverse("portal:invoice_pdf", args=[p.pk])).status_code, 403)
        self.login(self.course_admin)
        self.assertEqual(self.client.get(reverse("portal:invoice_pdf", args=[p.pk])).status_code, 403)
        self.login(self.owner)
        self.assertEqual(self.client.get(reverse("portal:invoice_pdf", args=[p.pk])).status_code, 200)

    def test_receipt_mode_even_with_gstin_saved_and_upi(self):
        s = SiteSettings.load()
        s.invoice_mode = "receipt"  # GSTIN stays saved; mode decides
        s.save()
        self.login(self.student)
        self.client.post(reverse("enrollments:upi_submit", args=[self.course.slug]), {
            "utr_number": "UTR998877665", "payer_name": "Sam", "paid_on": timezone.localdate().isoformat(),
            "amount": "1180", "screenshot": png()})
        self.login(self.owner)
        self.client.post(reverse("dashboard:upi_review", args=[UpiPaymentSubmission.objects.get().pk]), {"decision": "approve"})
        inv = Invoice.objects.get()
        self.assertFalse(inv.is_tax_invoice)
        self.assertEqual(inv.title, "Payment Receipt")
        self.assertEqual((inv.taxable_value, inv.tax_total), (Decimal("1180.00"), Decimal("0.00")))
        self.assertEqual(inv.payment_reference, "UTR998877665")

    def test_unpaid_has_no_invoice_and_backfill_command(self):
        unpaid = Payment.objects.create(student=self.student, course=self.course, gateway=PaymentGateway.RAZORPAY, amount=1180)
        self.login(self.student)
        self.assertEqual(self.client.get(reverse("portal:invoice_pdf", args=[unpaid.pk])).status_code, 404)
        old = Payment.objects.create(student=self.student, course=self.course, gateway=PaymentGateway.OFFLINE, amount=1180,
                                     status=PaymentStatus.PAID, paid_at=timezone.now())
        out = StringIO()
        call_command("issue_missing_invoices", stdout=out)
        self.assertIn("Issued 1 invoice", out.getvalue())
        self.assertTrue(Invoice.objects.filter(payment=old).exists())

    def test_settings_validate_gstin(self):
        self.login(self.owner)
        r = self.client.get(reverse("dashboard:settings"))
        form = r.context["form"]
        data = {k: ("on" if v is True else ("" if v in (None, False) else v)) for k, v in form.initial.items()
                if k not in ("logo", "upi_qr_code", "id")}
        data.update({"invoice_mode": "gst", "gstin": "", "business_state": "29"})
        self.assertContains(self.client.post(reverse("dashboard:settings"), data), "Enter your GSTIN")
        data.update({"gstin": "BADGSTIN", "business_state": "29"})
        self.assertContains(self.client.post(reverse("dashboard:settings"), data), "valid 15-character GSTIN")
        data.update({"gstin": "29ABCDE1234F1Z5", "business_state": "36"})
        self.assertContains(self.client.post(reverse("dashboard:settings"), data), "does not match")

    def test_switching_mode_affects_only_new_invoices(self):
        p1 = self.pay_razorpay(self.student)
        self.assertTrue(p1.invoice.is_tax_invoice)
        s = SiteSettings.load()
        s.invoice_mode = "receipt"
        s.save()
        p2 = self.pay_razorpay(self.outsider)
        self.assertFalse(p2.invoice.is_tax_invoice)
        p1.invoice.refresh_from_db()
        self.assertTrue(p1.invoice.is_tax_invoice)  # issued documents are never rewritten
