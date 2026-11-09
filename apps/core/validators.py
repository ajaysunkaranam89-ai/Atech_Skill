"""Upload validation: extension, size and magic-byte checks.

Extensions alone are trivially spoofed, so every validator also inspects the
first bytes of the file.
"""
import os

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator


def _ext(f):
    return os.path.splitext(f.name or "")[1].lower()


def _head(f, n=16):
    pos = f.tell() if hasattr(f, "tell") else 0
    try:
        f.seek(0)
        data = f.read(n)
    finally:
        try:
            f.seek(pos)
        except Exception:  # pragma: no cover
            pass
    return data or b""


def _check_size(f, max_mb):
    if f.size and f.size > max_mb * 1024 * 1024:
        raise ValidationError(f"File is too large. Maximum allowed size is {max_mb} MB.")


def _is_new_upload(f):
    # Already-stored FieldFiles are not re-validated on every save.
    return getattr(f, "_committed", True) is False or hasattr(f, "content_type")


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
VIDEO_EXTS = {".mp4", ".m4v"}
DOC_EXTS = {".pdf", ".zip", ".docx", ".pptx", ".xlsx", ".txt", ".md", ".csv"}


def validate_image_upload(f):
    if not _is_new_upload(f):
        return
    if _ext(f) not in IMAGE_EXTS:
        raise ValidationError("Only JPG, PNG, WEBP or GIF images are allowed.")
    _check_size(f, settings.MAX_IMAGE_UPLOAD_MB)
    head = _head(f, 12)
    if not (
        head.startswith(b"\xff\xd8\xff")
        or head.startswith(b"\x89PNG")
        or head.startswith(b"GIF8")
        or (head[:4] == b"RIFF" and head[8:12] == b"WEBP")
    ):
        raise ValidationError("The uploaded file is not a valid image.")


def validate_video_upload(f):
    if not _is_new_upload(f):
        return
    if _ext(f) not in VIDEO_EXTS:
        raise ValidationError("Only MP4 video files are allowed.")
    _check_size(f, settings.MAX_VIDEO_UPLOAD_MB)
    head = _head(f, 12)
    if head[4:8] != b"ftyp":
        raise ValidationError("The uploaded file is not a valid MP4 video.")


def validate_document_upload(f):
    if not _is_new_upload(f):
        return
    ext = _ext(f)
    if ext not in DOC_EXTS:
        raise ValidationError("Allowed types: " + ", ".join(sorted(DOC_EXTS)))
    _check_size(f, settings.MAX_DOCUMENT_UPLOAD_MB)
    head = _head(f, 8)
    if ext == ".pdf" and not head.startswith(b"%PDF"):
        raise ValidationError("The uploaded file is not a valid PDF.")
    if ext in {".zip", ".docx", ".pptx", ".xlsx"} and not head.startswith(b"PK"):
        raise ValidationError("The uploaded file is not a valid Office/ZIP document.")
    if ext in {".txt", ".md", ".csv"} and b"\x00" in _head(f, 1024):
        raise ValidationError("Text files must not contain binary data.")


def validate_pdf_upload(f):
    if not _is_new_upload(f):
        return
    if _ext(f) != ".pdf":
        raise ValidationError("Only PDF files are allowed.")
    validate_document_upload(f)


https_url = URLValidator(schemes=["https"])


def validate_https_url(value):
    https_url(value)


def validate_google_meet_url(value):
    https_url(value)
    if not value.startswith("https://meet.google.com/"):
        raise ValidationError("Enter a Google Meet link (https://meet.google.com/...).")
