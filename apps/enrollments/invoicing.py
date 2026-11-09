"""Invoice / receipt generation for verified payments.

* Numbers are sequential and gap-free per Indian financial year: PREFIX/2026-27/0001.
* Course prices are treated as GST-inclusive. Website settings > Invoice mode
  decides the document type. In "With GST" mode (GSTIN required) the invoice is a Tax Invoice with CGST+SGST (same state / state unknown) or IGST
  (buyer in another state). In "Without GST" mode it is a Payment Receipt with no tax lines.
* issue_invoice() is idempotent: calling it again for the same payment returns the
  existing invoice.
"""
from decimal import ROUND_HALF_UP, Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.core.models import SiteSettings

from .models import Invoice, InvoiceSequence, PaymentStatus

TWO = Decimal("0.01")


def financial_year(dt=None):
    d = timezone.localtime(dt or timezone.now()).date()
    start = d.year if d.month >= 4 else d.year - 1
    return f"{start}-{str(start + 1)[-2:]}"


def next_invoice_number(prefix, dt=None):
    fy = financial_year(dt)
    seq, _ = InvoiceSequence.objects.select_for_update().get_or_create(financial_year=fy)
    seq.last_number += 1
    seq.save(update_fields=["last_number"])
    return f"{prefix}/{fy}/{seq.last_number:04d}"


def split_tax(total, rate, seller_state, buyer_state, registered):
    """Return (taxable, cgst, sgst, igst) for a GST-inclusive total."""
    total = Decimal(total).quantize(TWO)
    rate = Decimal(rate or 0)
    if not registered or rate <= 0:
        return total, Decimal("0.00"), Decimal("0.00"), Decimal("0.00")
    taxable = (total * 100 / (100 + rate)).quantize(TWO, rounding=ROUND_HALF_UP)
    tax = total - taxable
    if buyer_state and seller_state and buyer_state != seller_state:
        return taxable, Decimal("0.00"), Decimal("0.00"), tax
    cgst = (tax / 2).quantize(TWO, rounding=ROUND_HALF_UP)
    return taxable, cgst, tax - cgst, Decimal("0.00")


_ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve",
         "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
_TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def _two(n):
    return _ONES[n] if n < 20 else (_TENS[n // 10] + (" " + _ONES[n % 10] if n % 10 else ""))


def _three(n):
    h, r = divmod(n, 100)
    parts = []
    if h:
        parts.append(_ONES[h] + " Hundred")
    if r:
        parts.append(_two(r))
    return " ".join(parts)


def amount_in_words(amount):
    """Indian numbering (lakh / crore): 125000.50 -> 'One Lakh Twenty Five Thousand Rupees and Fifty Paise Only'."""
    amount = Decimal(amount).quantize(TWO)
    rupees = int(amount)
    paise = int((amount - rupees) * 100)
    if rupees == 0:
        words = "Zero"
    else:
        crore, rest = divmod(rupees, 10_000_000)
        lakh, rest = divmod(rest, 100_000)
        thousand, rest = divmod(rest, 1000)
        parts = []
        if crore:
            parts.append(_three(crore) + " Crore")
        if lakh:
            parts.append(_two(lakh) + " Lakh")
        if thousand:
            parts.append(_two(thousand) + " Thousand")
        if rest:
            parts.append(_three(rest))
        words = " ".join(parts)
    out = f"{words} Rupees"
    if paise:
        out += f" and {_two(paise)} Paise"
    return out + " Only"


def issue_invoice(payment):
    """Create the invoice for a PAID payment (idempotent). Returns the Invoice or None."""
    if payment.status != PaymentStatus.PAID:
        return None
    existing = Invoice.objects.filter(payment=payment).first()
    if existing:
        return existing
    site = SiteSettings.load()
    student = payment.student
    profile = getattr(student, "student_profile", None)
    buyer_state = (profile.state if profile else "") or ""
    registered = site.invoice_mode == site.INVOICE_MODE_GST and bool(site.gstin.strip())
    taxable, cgst, sgst, igst = split_tax(payment.amount, site.gst_rate, site.business_state, buyer_state, registered)
    method = payment.get_gateway_display()
    reference = payment.razorpay_payment_id or payment.upi_reference or payment.razorpay_order_id or ""
    try:
        with transaction.atomic():
            issued = payment.paid_at or timezone.now()
            return Invoice.objects.create(
                payment=payment,
                number=next_invoice_number(site.invoice_prefix or "INV", issued),
                issued_at=issued,
                is_tax_invoice=registered,
                seller_name=site.legal_business_name or site.site_name,
                seller_address=site.address,
                seller_gstin=site.gstin.strip().upper() if registered else "",
                seller_state=site.business_state,
                seller_email=site.contact_email,
                seller_phone=site.contact_phone,
                buyer_name=student.get_full_name() or student.email,
                buyer_email=student.email,
                buyer_phone=student.phone,
                buyer_company=(profile.billing_name if profile else ""),
                buyer_gstin=(profile.billing_gstin.upper() if profile else ""),
                buyer_address=(profile.billing_address if profile else ""),
                buyer_state=buyer_state,
                description=f"Course fee: {payment.course_title or 'Training programme'}",
                sac_code=site.sac_code if registered else "",
                gst_rate=site.gst_rate if registered else 0,
                taxable_value=taxable,
                cgst=cgst,
                sgst=sgst,
                igst=igst,
                total=Decimal(payment.amount).quantize(TWO),
                payment_method=method,
                payment_reference=reference,
            )
    except IntegrityError:  # created concurrently (webhook + callback)
        return Invoice.objects.get(payment=payment)
