"""Operational health shown to the Super Admin on the dashboard (backups, email worker)."""
import json
from datetime import datetime

from django.conf import settings
from django.utils import timezone

from . import mail

BACKUP_STALE_HOURS = 30


def backup_status():
    """Read /app/backups/last.json written by the backup container."""
    path = settings.BACKUP_STATUS_FILE
    try:
        if not path.exists():
            return {"state": "missing", "message": "No backup has run yet."}
        data = json.loads(path.read_text())
        finished = datetime.fromisoformat(data["finished"])
    except Exception:
        return {"state": "error", "message": "Backup status file is unreadable."}
    age_h = (timezone.now() - finished).total_seconds() / 3600
    state = "ok" if data.get("ok") and age_h <= BACKUP_STALE_HOURS else ("failed" if not data.get("ok") else "stale")
    return {"state": state, "finished": finished, "age_hours": round(age_h, 1), "size_mb": round((data.get("size_kb") or 0) / 1024, 1),
            "name": data.get("path", ""), "message": data.get("message", "")}


def email_status():
    from .models import OutboundEmail

    if not settings.EMAIL_QUEUE:
        return {"queue": False}
    pending = OutboundEmail.objects.filter(status__in=[OutboundEmail.PENDING, OutboundEmail.SENDING]).count()
    failed = OutboundEmail.objects.filter(status=OutboundEmail.FAILED).count()
    seen = mail.worker_last_seen()
    return {"queue": True, "pending": pending, "failed": failed,
            "worker_ok": seen is not None and seen < 120, "worker_seen_s": None if seen is None else int(seen)}
