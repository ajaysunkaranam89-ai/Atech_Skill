"""Time-based one-time passwords (RFC 6238) for staff two-factor login.

Compatible with Google Authenticator, Microsoft Authenticator, Authy, 1Password, etc.
Implemented on the standard library only (hmac/hashlib/base64).
"""
import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

DIGITS = 6
PERIOD = 30
WINDOW = 1  # accept the previous/next 30-second step to allow for phone clock drift


def new_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _key(secret):
    s = secret.strip().replace(" ", "").upper()
    return base64.b32decode(s + "=" * (-len(s) % 8))


def code_at(secret, counter):
    digest = hmac.new(_key(secret), struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10 ** DIGITS).zfill(DIGITS)


def current_counter(now=None):
    return int((now if now is not None else time.time()) // PERIOD)


def verify(secret, code, last_counter=0, now=None):
    """Return the matched counter (int) or None. Codes at or before last_counter are rejected (no replay)."""
    code = "".join(ch for ch in str(code or "") if ch.isdigit())
    if not secret or len(code) != DIGITS:
        return None
    base = current_counter(now)
    for c in range(base - WINDOW, base + WINDOW + 1):
        if c > (last_counter or 0) and hmac.compare_digest(code_at(secret, c), code):
            return c
    return None


def provisioning_uri(secret, account, issuer):
    label = quote(f"{issuer}:{account}")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}&algorithm=SHA1&digits={DIGITS}&period={PERIOD}"


def qr_svg(data, size=200):
    """Render a QR code as inline SVG using ReportLab (already a dependency for PDFs)."""
    from reportlab.graphics import renderSVG
    from reportlab.graphics.barcode.qr import QrCodeWidget
    from reportlab.graphics.shapes import Drawing

    widget = QrCodeWidget(data)
    x1, y1, x2, y2 = widget.getBounds()
    d = Drawing(size, size, transform=[size / (x2 - x1), 0, 0, size / (y2 - y1), 0, 0])
    d.add(widget)
    svg = renderSVG.drawToString(d)
    return svg[svg.index("<svg"):]


# ------------------------------------------------------------------ recovery codes
def new_recovery_codes(n=8):
    codes = [f"{secrets.token_hex(2)}-{secrets.token_hex(2)}-{secrets.token_hex(2)}" for _ in range(n)]
    return codes, [_hash_code(c) for c in codes]


def _hash_code(code):
    return hashlib.sha256(code.strip().lower().replace(" ", "").encode()).hexdigest()


def use_recovery_code(user, code):
    """Consume a recovery code. Returns True if valid (and removes it)."""
    h = _hash_code(code)
    codes = list(user.totp_recovery_codes or [])
    for stored in codes:
        if hmac.compare_digest(stored, h):
            codes.remove(stored)
            user.totp_recovery_codes = codes
            user.save(update_fields=["totp_recovery_codes"])
            return True
    return False
