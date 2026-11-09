"""Database-backed outgoing email queue.

`QueuedEmailBackend` is set as Django's EMAIL_BACKEND in production, so every
send_mail()/EmailMessage in the app (notifications, verification, password
reset, lead alerts, error reports) is stored instantly instead of blocking the
web request. The `email_worker` process (its own container) delivers them with
the real backend (EMAIL_DELIVERY_BACKEND, e.g. SMTP) and retries failures.
"""
import logging
import time
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.core.mail import EmailMultiAlternatives, get_connection
from django.core.mail.backends.base import BaseEmailBackend
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

RETRY_DELAYS_MIN = [1, 5, 15, 60, 240]  # after the 5th failure the email is marked failed
HEARTBEAT_KEY = "email_worker_heartbeat"


class QueuedEmailBackend(BaseEmailBackend):
    def send_messages(self, email_messages):
        from .models import OutboundEmail

        n = 0
        for m in email_messages or []:
            recipients = m.recipients()
            if not recipients:
                continue
            OutboundEmail.objects.create(
                subject=str(m.subject)[:255],
                body=m.body or "",
                html=next((c for c, mt in getattr(m, "alternatives", []) if mt == "text/html"), ""),
                from_email=m.from_email or settings.DEFAULT_FROM_EMAIL,
                to=list(m.to), cc=list(m.cc), bcc=list(m.bcc), reply_to=list(m.reply_to),
            )
            if m.attachments:
                logger.warning("Queued email %r had attachments; they are not supported by the queue", m.subject)
            n += 1
        return n


def _build(e):
    msg = EmailMultiAlternatives(
        subject=e.subject, body=e.body, from_email=e.from_email, to=e.to, cc=e.cc, bcc=e.bcc, reply_to=e.reply_to,
    )
    if e.html:
        msg.attach_alternative(e.html, "text/html")
    return msg


def deliver_pending(limit=50):
    """Send due emails. Safe to run in several workers (rows are locked with SKIP LOCKED)."""
    from .models import OutboundEmail

    now = timezone.now()
    with transaction.atomic():
        qs = OutboundEmail.objects.filter(status=OutboundEmail.PENDING, next_attempt_at__lte=now).order_by("created_at")
        if connection_supports_skip_locked():
            qs = qs.select_for_update(skip_locked=True)
        batch = list(qs[:limit])
        OutboundEmail.objects.filter(pk__in=[e.pk for e in batch]).update(status=OutboundEmail.SENDING)
    if not batch:
        return 0, 0
    sent = failed = 0
    conn = get_connection(settings.EMAIL_DELIVERY_BACKEND)
    try:
        conn.open()
    except Exception as exc:  # SMTP down: put everything back for a later retry
        for e in batch:
            _mark_failure(e, exc)
        return 0, len(batch)
    try:
        for e in batch:
            try:
                conn.send_messages([_build(e)])
                e.status = OutboundEmail.SENT
                e.sent_at = timezone.now()
                e.attempts += 1
                e.last_error = ""
                e.save(update_fields=["status", "sent_at", "attempts", "last_error"])
                sent += 1
            except Exception as exc:
                _mark_failure(e, exc)
                failed += 1
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return sent, failed


def _mark_failure(e, exc):
    from .models import OutboundEmail

    e.attempts += 1
    e.last_error = f"{exc.__class__.__name__}: {exc}"[:500]
    if e.attempts > len(RETRY_DELAYS_MIN):
        e.status = OutboundEmail.FAILED
    else:
        e.status = OutboundEmail.PENDING
        e.next_attempt_at = timezone.now() + timedelta(minutes=RETRY_DELAYS_MIN[e.attempts - 1])
    e.save(update_fields=["attempts", "last_error", "status", "next_attempt_at"])
    logger.warning("Email %s to %s failed (attempt %s): %s", e.pk, e.to, e.attempts, e.last_error)


def connection_supports_skip_locked():
    from django.db import connection

    return connection.features.has_select_for_update_skip_locked


def beat():
    cache.set(HEARTBEAT_KEY, time.time(), 3600)


def worker_last_seen():
    ts = cache.get(HEARTBEAT_KEY)
    return None if ts is None else time.time() - ts


def cleanup(days=30):
    from .models import OutboundEmail

    return OutboundEmail.objects.filter(status=OutboundEmail.SENT, sent_at__lt=timezone.now() - timedelta(days=days)).delete()[0]
