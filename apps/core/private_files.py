"""Serve files from private storage only after a permission check.

Production: Django returns an empty response with X-Accel-Redirect and Nginx
streams the file from an `internal` location (supports Range requests, so
video seeking works, and keeps gunicorn workers free).
Development: Django streams the file itself with basic Range support.
"""
import mimetypes
import os
import re
from urllib.parse import quote

from django.conf import settings
from django.http import FileResponse, Http404, HttpResponse, StreamingHttpResponse

RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")


def _disposition(filename, download):
    kind = "attachment" if download else "inline"
    return f"{kind}; filename*=UTF-8''{quote(filename)}"


def serve_private(request, fieldfile, download=False, filename=None):
    if not fieldfile or not fieldfile.name:
        raise Http404("File not available")
    name = fieldfile.name
    filename = filename or os.path.basename(name)
    ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"

    if settings.USE_X_ACCEL_REDIRECT:
        resp = HttpResponse(content_type=ctype)
        resp["X-Accel-Redirect"] = settings.X_ACCEL_PREFIX + quote(name)
        resp["Content-Disposition"] = _disposition(filename, download)
        resp["Cache-Control"] = "private, no-store"
        return resp

    path = fieldfile.storage.path(name)
    if not os.path.exists(path):
        raise Http404("File not available")
    size = os.path.getsize(path)
    rng = request.headers.get("Range", "")
    m = RANGE_RE.match(rng) if rng else None
    if m and (m.group(1) or m.group(2)):
        if m.group(1):
            start = int(m.group(1))
            end = int(m.group(2)) if m.group(2) else size - 1
        else:  # suffix range: last N bytes
            start = max(size - int(m.group(2)), 0)
            end = size - 1
        end = min(end, size - 1)
        if start > end:
            resp = HttpResponse(status=416)
            resp["Content-Range"] = f"bytes */{size}"
            return resp
        length = end - start + 1

        def chunks():
            with open(path, "rb") as fh:
                fh.seek(start)
                remaining = length
                while remaining > 0:
                    data = fh.read(min(64 * 1024, remaining))
                    if not data:
                        break
                    remaining -= len(data)
                    yield data

        resp = StreamingHttpResponse(chunks(), status=206, content_type=ctype)
        resp["Content-Range"] = f"bytes {start}-{end}/{size}"
        resp["Content-Length"] = str(length)
    else:
        resp = FileResponse(open(path, "rb"), content_type=ctype)
        resp["Content-Length"] = str(size)
    resp["Accept-Ranges"] = "bytes"
    resp["Content-Disposition"] = _disposition(filename, download)
    resp["Cache-Control"] = "private, no-store"
    return resp
