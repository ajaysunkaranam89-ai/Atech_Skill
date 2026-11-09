"""Create invoices for paid payments that do not have one yet (e.g. paid before invoicing existed)."""
from django.core.management.base import BaseCommand

from apps.enrollments.invoicing import issue_invoice
from apps.enrollments.models import Payment, PaymentStatus


class Command(BaseCommand):
    help = "Issue invoices/receipts for all PAID payments that are missing one, oldest first."

    def handle(self, *args, **opts):
        n = 0
        for p in Payment.objects.filter(status=PaymentStatus.PAID, invoice__isnull=True).order_by("paid_at", "pk"):
            inv = issue_invoice(p)
            self.stdout.write(f"{inv.number}  {p.student.email}  Rs.{p.amount}")
            n += 1
        self.stdout.write(self.style.SUCCESS(f"Issued {n} invoice(s)."))
