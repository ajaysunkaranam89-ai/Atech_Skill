"""PDF rendering (ReportLab) for certificates and invoices/receipts.

Uses the built-in Helvetica fonts so no system font packages are needed; the
rupee symbol is written as "Rs." because Helvetica has no ₹ glyph.
"""
import re
from io import BytesIO

from django.conf import settings
from django.utils import timezone
from reportlab.graphics import renderPDF
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .india import STATE_NAMES

NAVY = colors.HexColor("#000f2f")
CYAN = colors.HexColor("#00b4d8")
BLUE = colors.HexColor("#1565c0")
MUTED = colors.HexColor("#6b7a90")
LOGO = settings.BASE_DIR / "static" / "img" / "logo-pdf.png"


def _logo_reader():
    try:
        return ImageReader(str(LOGO))
    except Exception:
        return None


def _qr(url, size):
    widget = QrCodeWidget(url)
    x1, y1, x2, y2 = widget.getBounds()
    d = Drawing(size, size, transform=[size / (x2 - x1), 0, 0, size / (y2 - y1), 0, 0])
    d.add(widget)
    return d


def _register_fonts():
    """Use DejaVu Sans (has the ₹ glyph) when installed; otherwise Helvetica with "Rs."."""
    import os

    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    dirs = [os.environ.get("PDF_FONT_DIR", ""), "/usr/share/fonts/truetype/dejavu", str(settings.BASE_DIR / "static" / "fonts")]
    for d in dirs:
        reg, bold = os.path.join(d, "DejaVuSans.ttf"), os.path.join(d, "DejaVuSans-Bold.ttf")
        if d and os.path.exists(reg) and os.path.exists(bold):
            pdfmetrics.registerFont(TTFont("DejaVuSans", reg))
            pdfmetrics.registerFont(TTFont("DejaVuSans-Bold", bold))
            return "DejaVuSans", "DejaVuSans-Bold", "DejaVuSans", "₹"
    return "Helvetica", "Helvetica-Bold", "Helvetica-Oblique", "Rs. "


REG, BOLD, ITALIC, CURRENCY = _register_fonts()


def indian_grouping(n):
    """1234567.5 -> '12,34,567.50' (Indian lakh/crore grouping)."""
    whole, frac = f"{abs(n):.2f}".split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        head = ",".join(re.findall(r"\d{1,2}", head[::-1]))[::-1] if head else ""
        whole = head + "," + tail
    return ("-" if n < 0 else "") + whole + "." + frac


def money(v):
    return f"{CURRENCY}{indian_grouping(float(v))}"


# ------------------------------------------------------------------ certificate
def certificate_pdf(cert, verify_url, site_name, tagline):
    buf = BytesIO()
    from reportlab.pdfgen import canvas

    w, h = landscape(A4)
    c = canvas.Canvas(buf, pagesize=(w, h))
    c.setTitle(f"Certificate {cert.certificate_number}")
    c.setAuthor(site_name)

    # Frame
    c.setFillColor(colors.white)
    c.rect(0, 0, w, h, stroke=0, fill=1)
    c.setStrokeColor(NAVY)
    c.setLineWidth(14)
    c.rect(18, 18, w - 36, h - 36)
    c.setStrokeColor(CYAN)
    c.setLineWidth(2)
    c.rect(34, 34, w - 68, h - 68)
    # Corner accents
    c.setFillColor(CYAN)
    for x, y in [(34, h - 40), (w - 74, h - 40), (34, 34), (w - 74, 34)]:
        c.rect(x, y, 40, 6, stroke=0, fill=1)

    logo = _logo_reader()
    if logo:
        iw, ih = logo.getSize()
        lw = 250
        lh = lw * ih / iw
        c.drawImage(logo, (w - lw) / 2, h - 70 - lh, lw, lh, mask="auto")
        top = h - 80 - lh
    else:
        c.setFont(BOLD, 26)
        c.setFillColor(NAVY)
        c.drawCentredString(w / 2, h - 100, site_name)
        top = h - 120

    c.setFillColor(MUTED)
    c.setFont(BOLD, 13)
    c.drawCentredString(w / 2, top - 22, "C E R T I F I C A T E   O F   C O M P L E T I O N")

    c.setFont(REG, 13)
    c.setFillColor(colors.black)
    c.drawCentredString(w / 2, top - 58, "This is to certify that")

    name = cert.student.get_full_name() or cert.student.email
    size = 34 if len(name) < 28 else 26
    c.setFont(BOLD, size)
    c.setFillColor(NAVY)
    c.drawCentredString(w / 2, top - 100, name)
    c.setStrokeColor(CYAN)
    c.setLineWidth(1.5)
    c.line(w / 2 - 190, top - 110, w / 2 + 190, top - 110)

    c.setFont(REG, 13)
    c.setFillColor(colors.black)
    c.drawCentredString(w / 2, top - 138, "has successfully completed the course")
    c.setFont(BOLD, 22)
    c.setFillColor(BLUE)
    c.drawCentredString(w / 2, top - 170, cert.course_title)
    if cert.grade:
        c.setFont(REG, 13)
        c.setFillColor(colors.black)
        c.drawCentredString(w / 2, top - 194, f"with grade {cert.grade}")

    # Footer row: date | tagline | QR + number
    y = 70
    c.setFillColor(MUTED)
    c.setFont(REG, 10)
    c.drawString(70, y + 30, "Issued on")
    c.setFillColor(NAVY)
    c.setFont(BOLD, 13)
    c.drawString(70, y + 12, timezone.localtime(cert.issued_at).strftime("%d %B %Y"))

    c.setFillColor(MUTED)
    c.setFont(ITALIC, 11)
    c.drawCentredString(w / 2, y + 12, tagline)

    qr_size = 74
    renderPDF.draw(_qr(verify_url, qr_size), c, w - 70 - qr_size, y - 6)
    c.setFillColor(MUTED)
    c.setFont(REG, 9)
    c.drawRightString(w - 80 - qr_size, y + 40, "Certificate No.")
    c.setFillColor(NAVY)
    c.setFont(BOLD, 12)
    c.drawRightString(w - 80 - qr_size, y + 24, cert.certificate_number)
    c.setFillColor(MUTED)
    c.setFont(REG, 8)
    c.drawRightString(w - 80 - qr_size, y + 10, "Scan to verify")
    c.drawRightString(w - 80 - qr_size, y, verify_url.replace("https://", "").replace("http://", ""))

    if cert.is_revoked:
        c.saveState()
        c.setFillColor(colors.Color(0.85, 0.1, 0.1, alpha=0.25))
        c.setFont(BOLD, 90)
        c.translate(w / 2, h / 2)
        c.rotate(25)
        c.drawCentredString(0, 0, "REVOKED")
        c.restoreState()

    c.showPage()
    c.save()
    return buf.getvalue()


# ------------------------------------------------------------------ invoice / receipt
def invoice_pdf(inv, amount_words):
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=16 * mm,
        title=f"{inv.title} {inv.number}", author=inv.seller_name,
    )
    base = ParagraphStyle("b", fontName=REG, fontSize=9.5, leading=13)
    small = ParagraphStyle("s", parent=base, fontSize=8.5, leading=11.5, textColor=MUTED)
    bold = ParagraphStyle("bd", parent=base, fontName=BOLD)
    h1 = ParagraphStyle("h1", parent=base, fontName=BOLD, fontSize=18, leading=22, textColor=NAVY, alignment=TA_RIGHT)
    right = ParagraphStyle("r", parent=base, alignment=TA_RIGHT)
    label = ParagraphStyle("l", parent=small, fontName=BOLD, textColor=NAVY)

    def p(text, style=base):
        from django.utils.html import escape

        return Paragraph(escape(text or "").replace("\n", "<br/>"), style)

    story = []
    logo_cell = ""
    if LOGO.exists():
        img = Image(str(LOGO))
        ratio = img.imageHeight / img.imageWidth
        img.drawWidth = 62 * mm
        img.drawHeight = 62 * mm * ratio
        logo_cell = img
    head = Table(
        [[logo_cell, [Paragraph(inv.title.upper(), h1),
                      Paragraph(f"<b>No:</b> {inv.number}", right),
                      Paragraph(f"<b>Date:</b> {timezone.localtime(inv.issued_at):%d %b %Y}", right)]]],
        colWidths=[90 * mm, 84 * mm],
    )
    head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    story += [head, Spacer(1, 6)]

    seller = [Paragraph("FROM", label), p(inv.seller_name, bold), p(inv.seller_address)]
    if inv.seller_state:
        seller.append(p(f"State: {STATE_NAMES.get(inv.seller_state, '')} ({inv.seller_state})"))
    if inv.seller_gstin:
        seller.append(p(f"GSTIN: {inv.seller_gstin}", bold))
    if inv.seller_email or inv.seller_phone:
        seller.append(p(" · ".join(x for x in [inv.seller_email, inv.seller_phone] if x), small))
    buyer = [Paragraph("BILL TO", label), p(inv.buyer_company or inv.buyer_name, bold)]
    if inv.buyer_company:
        buyer.append(p(f"Attn: {inv.buyer_name}"))
    if inv.buyer_address:
        buyer.append(p(inv.buyer_address))
    if inv.buyer_state:
        buyer.append(p(f"State: {STATE_NAMES.get(inv.buyer_state, '')} ({inv.buyer_state})"))
    if inv.buyer_gstin:
        buyer.append(p(f"GSTIN: {inv.buyer_gstin}", bold))
    buyer.append(p(" · ".join(x for x in [inv.buyer_email, inv.buyer_phone] if x), small))
    parties = Table([[seller, buyer]], colWidths=[87 * mm, 87 * mm])
    parties.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOX", (0, 0), (0, 0), 0.6, colors.HexColor("#dbe3ee")),
        ("BOX", (1, 0), (1, 0), 0.6, colors.HexColor("#dbe3ee")),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f7f9fc")),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    story += [parties, Spacer(1, 10)]

    if inv.is_tax_invoice:
        header = ["#", "Description", "SAC", "Taxable value", f"GST {inv.gst_rate:g}%", "Amount"]
        row = ["1", p(inv.description), inv.sac_code, money(inv.taxable_value), money(inv.tax_total), money(inv.total)]
        widths = [8 * mm, 74 * mm, 18 * mm, 26 * mm, 22 * mm, 26 * mm]
    else:
        header = ["#", "Description", "Amount"]
        row = ["1", p(inv.description), money(inv.total)]
        widths = [8 * mm, 140 * mm, 26 * mm]
    items = Table([header, row], colWidths=widths, repeatRows=1)
    items.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), BOLD), ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (-1, 0), (-1, -1), "RIGHT"), ("ALIGN", (-3, 0), (-2, -1), "RIGHT") if inv.is_tax_invoice else ("ALIGN", (0, 0), (0, 0), "LEFT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, -1), (-1, -1), 0.6, colors.HexColor("#dbe3ee")),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story += [items, Spacer(1, 8)]

    totals = []
    if inv.is_tax_invoice:
        totals.append(["Taxable value", money(inv.taxable_value)])
        if inv.igst:
            totals.append([f"IGST @ {inv.gst_rate:g}%", money(inv.igst)])
        else:
            half = inv.gst_rate / 2
            totals += [[f"CGST @ {half:g}%", money(inv.cgst)], [f"SGST @ {half:g}%", money(inv.sgst)]]
    totals.append(["Total paid", money(inv.total)])
    tt = Table(totals, colWidths=[40 * mm, 30 * mm], hAlign="RIGHT")
    tt.setStyle(TableStyle([
        ("ALIGN", (1, 0), (1, -1), "RIGHT"), ("FONTSIZE", (0, 0), (-1, -1), 9.5),
        ("FONTNAME", (0, -1), (-1, -1), BOLD), ("TEXTCOLOR", (0, -1), (-1, -1), NAVY),
        ("LINEABOVE", (0, -1), (-1, -1), 1, NAVY), ("TOPPADDING", (0, 0), (-1, -1), 3),
    ]))
    story += [tt, Spacer(1, 6), p(f"Amount in words: {amount_words}", bold), Spacer(1, 10)]

    pay = Table([[p("Payment method", label), p("Reference", label), p("Status", label)],
                 [p(inv.payment_method), p(inv.payment_reference or "—"), p("PAID", bold)]],
                colWidths=[58 * mm, 80 * mm, 36 * mm])
    pay.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#dbe3ee")),
                             ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f7f9fc"))]))
    story += [pay, Spacer(1, 14)]

    notes = ["This is a computer-generated document and does not require a signature."]
    if inv.is_tax_invoice:
        notes.insert(0, "Place of supply: " + (STATE_NAMES.get(inv.buyer_state or inv.seller_state, "") or "—")
                     + ". Tax is not payable on reverse charge basis.")
    else:
        notes.insert(0, "No GST has been charged on this transaction.")
    for n in notes:
        story.append(p(n, small))
    doc.build(story)
    return buf.getvalue()


# ------------------------------------------------------------------ course brochure
def brochure_pdf(course, site):
    from django.conf import settings as dj

    from apps.courses.access import student_outline

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=14 * mm,
                            bottomMargin=16 * mm, title=f"{course.title} – brochure", author=site.site_name)
    base = ParagraphStyle("b", fontName=REG, fontSize=10, leading=14)
    small = ParagraphStyle("s", parent=base, fontSize=8.5, leading=11.5, textColor=MUTED)
    h1 = ParagraphStyle("h1", parent=base, fontName=BOLD, fontSize=22, leading=27, textColor=NAVY)
    h2 = ParagraphStyle("h2", parent=base, fontName=BOLD, fontSize=13, leading=17, textColor=NAVY, spaceBefore=10, spaceAfter=4)
    bullet = ParagraphStyle("li", parent=base, leftIndent=12, bulletIndent=2)

    from django.utils.html import escape

    def p(t, s=base):
        return Paragraph(escape(t or "").replace("\n", "<br/>"), s)

    story = []
    if LOGO.exists():
        img = Image(str(LOGO))
        img.drawWidth = 60 * mm
        img.drawHeight = 60 * mm * img.imageHeight / img.imageWidth
        img.hAlign = "LEFT"
        story += [img, Spacer(1, 8)]
    story += [p(course.title, h1), p(course.short_description, ParagraphStyle("lead", parent=base, fontSize=11.5, leading=16, textColor=MUTED)), Spacer(1, 8)]

    facts = [["Duration", course.duration_text or (f"{course.duration_hours} hours" if course.duration_hours else "—")],
             ["Level", course.get_level_display()], ["Mode", course.get_mode_display()], ["Language", course.language],
             ["Fee", "Free" if course.is_free else money(course.effective_price)],
             ["Certificate", "Yes, verifiable online" if course.certificate_enabled else "—"]]
    ft = Table(facts, colWidths=[38 * mm, 136 * mm])
    ft.setStyle(TableStyle([("FONTNAME", (0, 0), (0, -1), BOLD), ("TEXTCOLOR", (0, 0), (0, -1), NAVY),
                            ("FONTSIZE", (0, 0), (-1, -1), 10), ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f4f7fb")),
                            ("LINEBELOW", (0, 0), (-1, -2), 0.4, colors.HexColor("#dbe3ee")),
                            ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
    story.append(ft)

    def bullets(title, items):
        if items:
            story.append(p(title, h2))
            story.extend(Paragraph(escape(i), bullet, bulletText="•") for i in items)

    bullets("What you'll learn", course.objectives_list)
    outline = student_outline(course)
    if outline:
        story.append(p("Curriculum", h2))
        for i, m in enumerate(outline, 1):
            story.append(Paragraph(f"<b>Module {i}: {escape(m.title)}</b>", base))
            for lesson in m.visible_lessons:
                story.append(Paragraph(escape(lesson.title), bullet, bulletText="–"))
    else:
        bullets("Syllabus", course.syllabus_list)
    bullets("Prerequisites", course.prerequisites_list)
    bullets("Who should join", course.audience_list)
    trainers = list(course.trainers.filter(is_public=True).select_related("user"))
    if trainers:
        story.append(p("Your trainer" + ("s" if len(trainers) > 1 else ""), h2))
        for t in trainers:
            line = t.user.get_full_name() + (f" – {t.designation}" if t.designation else "")
            if t.experience_years:
                line += f" ({t.experience_years}+ years)"
            story.append(p(line))
    story += [Spacer(1, 12), p("Enroll / book a free demo", h2),
              p(f"{dj.SITE_URL}{course.get_absolute_url()}")]
    contact = " · ".join(x for x in [site.contact_phone, site.contact_email] if x)
    if contact:
        story.append(p(contact))
    story += [Spacer(1, 10), p(f"{site.site_name} – {site.tagline}. Fees and schedules may change; see the website for current details.", small)]
    doc.build(story)
    return buf.getvalue()
