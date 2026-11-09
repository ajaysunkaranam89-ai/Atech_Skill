"""Standard policy templates used when Website settings leaves a policy blank.

They are written for an Indian online IT-training business that takes payments
through Razorpay / UPI. The academy owner should review them (ideally with a
legal adviser) and replace them from Admin > Website settings when ready.
"""
from django.utils import timezone

PRIVACY = """Last updated: {updated}

{business} ("we", "us") operates {site_name} at {site_url}. This policy explains what personal information we collect and how we use it.

## Information we collect

* Account details: name, email address, phone number and password (stored encrypted).
* Learning data: courses enrolled, lessons viewed and completed, assignment submissions, certificates.
* Payment details: transaction reference, amount and status. Card, UPI and bank details are processed by Razorpay or your UPI app and are never stored by us.
* UPI payment screenshots and UTR numbers you submit for manual verification.
* Enquiry details you submit (name, phone, email, course interest, message) so we can respond to you.
* Technical data: IP address, browser type, login times and the website or campaign that referred you, used for security and to improve our outreach.

## How we use your information

* To create and manage your account and provide access to courses you enroll in.
* To verify payments and activate enrollments.
* To send course updates, live-class schedules, assignment feedback and certificates.
* To keep the platform secure and prevent misuse.

## Sharing

We do not sell your personal information. We share it only with service providers needed to run the platform (payment gateway, email delivery, hosting), or when required by law.

## Cookies

We use essential cookies to keep you logged in and protect forms. We may use Google Analytics cookies to understand how visitors use the site (IP addresses are anonymised). We do not sell your data or use it for third-party advertising.

## Data retention

We keep account, enrollment and payment records for as long as your account is active and as required by tax and accounting laws.

## Your rights

You can view and update your profile at any time. To request a copy or deletion of your data, email {email}.

## Contact

{contact_block}
"""

TERMS = """Last updated: {updated}

These terms apply to your use of {site_name} ({site_url}), operated by {business}. By creating an account or enrolling in a course you agree to them.

## Accounts

* You must provide accurate information and keep your password confidential.
* One account is for one learner. Sharing logins or course access is not allowed and may lead to suspension without refund.

## Courses and access

* Course fees, duration and access period are shown on each course page.
* Access to recorded lessons is for the period stated on the course (or lifetime if stated).
* Live classes are held on Google Meet according to the batch schedule. Schedules may change; we will notify you in advance.
* We may update course content to keep it current.

## Payments

* Payments are accepted through Razorpay and UPI. Enrollment is activated only after payment is verified.
* Prices are in Indian Rupees (INR).

## Intellectual property

All videos, notes, assignments and other materials are owned by {business}. You may use them for personal learning only. Downloading, recording, copying, sharing or reselling course content is prohibited.

## Conduct

Be respectful in live classes and communications. We may remove learners who disrupt classes or misuse the platform.

## Certificates

Certificates are issued at the academy's discretion on completing course requirements and can be verified online.

## Limitation of liability

Training is provided to help you build skills. We do not guarantee employment, certification exam results or specific outcomes.

## Changes

We may update these terms. Continued use after changes means you accept them.

## Governing law

These terms are governed by the laws of India.

## Contact

{contact_block}
"""

REFUND = """Last updated: {updated}

## Refund eligibility

* You may request a full refund within **{days} days** of enrollment, provided you have attended no more than 2 live classes and watched less than 20% of the recorded content.
* After {days} days, or once the above limits are exceeded, fees are non-refundable.
* If we cancel a batch or course, you will receive a full refund or a free transfer to another batch, at your choice.

## How to request a refund

Email {email} from your registered email address with your name, course and payment reference (Razorpay payment ID or UPI UTR number).

## Processing time

Approved refunds are processed within 5–7 working days to the original payment method. The time for the amount to reach your account depends on your bank.

## Batch transfers

You may request a transfer to a later batch of the same course once, subject to seat availability.

## Contact

{contact_block}
"""


def _contact_block(s):
    lines = [f"**{s.legal_business_name or s.site_name}**"]
    if s.address:
        lines += [line for line in s.address.splitlines() if line.strip()]
    if s.contact_email:
        lines.append(f"Email: {s.contact_email}")
    if s.contact_phone:
        lines.append(f"Phone: {s.contact_phone}")
    return "\n".join(lines)


def render_policy(kind, settings_obj, site_url):
    s = settings_obj
    custom = {"privacy": s.privacy_policy, "terms": s.terms_conditions, "refund": s.refund_policy}[kind]
    if custom.strip():
        return custom
    template = {"privacy": PRIVACY, "terms": TERMS, "refund": REFUND}[kind]
    return template.format(
        site_name=s.site_name,
        business=s.legal_business_name or s.site_name,
        site_url=site_url,
        email=s.contact_email or "our support email",
        days=s.refund_window_days,
        updated=timezone.localtime(s.updated_at).strftime("%d %B %Y") if s.updated_at else "",
        contact_block=_contact_block(s),
    )
