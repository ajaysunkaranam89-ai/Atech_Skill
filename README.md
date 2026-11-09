# A TechSkills Academy — LMS

**Learn. Practice. Build Your IT Career.**

An administrator-managed online IT training academy: Django + PostgreSQL + Bootstrap 5, deployed with Docker Compose (Gunicorn + Nginx). Every course, module, lesson, recorded video, PDF, assignment, batch and live class is created from the **Admin Dashboard**. Nothing is hard-coded, and no demo courses are created.

## Quick start (Docker)

```bash
cp .env.example .env        # then edit every CHANGE_ME value
docker compose up -d --build
```

Open `http://<server>/accounts/login/` and sign in with `DJANGO_SUPERADMIN_EMAIL` / `DJANGO_SUPERADMIN_PASSWORD` from `.env` (created on first start). Change that password right away.

Full production guide (Ubuntu and Rocky Linux, HTTPS, backups): **[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)**
Admin how-to (adding courses, replacing videos, notifications, payments): **[docs/ADMIN_GUIDE.md](docs/ADMIN_GUIDE.md)**

## Features

| Area | What you get |
|---|---|
| Course management | Create, edit, price and discount, syllabus, objectives, prerequisites, trainers, categories. Status: Draft / Published / Unpublished / Archived. Delete requires typing the course name, and enrollment and payment history is kept |
| Course builder | Modules → Lessons with **drag-and-drop ordering** (lessons can move between modules), plus per-item publish and unpublish |
| Recorded videos | YouTube unlisted/watch, YouTube embed, external HTTPS, uploaded MP4 (private storage). **Versioned**: replacing a video creates v2, v3… and students see the new version immediately. Roll back to any stored version. Admin preview of unpublished content. Upload progress bar |
| Study material | PDF and other files and links per lesson. Downloads are permission-checked. Replacing a file bumps its version |
| Notifications | Notify all enrolled students, selected batches, or publish silently. Dashboard plus email. **No duplicates** when the same change is saved again (content-fingerprint de-duplication) |
| Batches & live classes | Batches with capacity and trainer, Google Meet sessions with batch notifications and a "Join now" button |
| Students | Dashboard with progress, recently updated lessons, upcoming classes, assignments, materials, certificates |
| Payments | Razorpay Checkout (server-side order, signature verification, webhook with HMAC, amount check, idempotent) and manual UPI with admin approval. Access activates only after verification |
| Assignments & certificates | Submissions, grading and feedback. Certificates with a public verification URL and a printable layout |
| Admin | 15 dashboard sections with search, filters, sorting, pagination, reports, CSV export, website settings and audit log |
| Security | RBAC (Super Admin / Course Admin / Trainer / Student), CSRF, auto-escaping, login rate limiting (app + Nginx), magic-byte upload validation, private media via X-Accel-Redirect, secure cookies and HSTS on HTTPS, environment-based secrets, audit trail |

## Roles

| Role | Can do |
|---|---|
| Super Admin | Everything: administrators, payments, UPI approval, enrollments, reports, website settings, audit log, delete courses |
| Course Administrator | All courses, modules, lessons, videos, materials, batches, students, certificates |
| Trainer | Only assigned courses: modules, lessons, videos, materials, assignments and grading, live classes, announcements |
| Student | Enrolled courses, authorised lessons and downloads, assignments, certificates |

## About video protection

Unlisted YouTube links are **not** access control: anyone who has the link can watch. Uploaded MP4s are stored outside the public media folder and streamed only after the server checks enrollment, through Nginx `internal` + `X-Accel-Redirect`, with Range support for seeking. For high-value paid content, use a protected video platform with signed, expiring URLs and DRM (Bunny Stream, Mux, Cloudflare Stream, VdoCipher, Vimeo OTT) through the *External HTTPS URL* source. See `docs/DEPLOYMENT.md` §8.

## Development

```bash
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt   # Windows (Linux: .venv/bin/pip)
set DJANGO_DEBUG=1            # Linux: export DJANGO_DEBUG=1  (uses SQLite when POSTGRES_DB is unset)
python manage.py migrate && python manage.py createcachetable
python manage.py createsuperuser
python manage.py runserver
```

## Tests

71 automated tests cover every scenario in the specification, plus payments, RBAC and security.

```bash
./scripts/run_tests.sh                       # inside Docker against PostgreSQL
DJANGO_DEBUG=1 python manage.py test tests   # local (SQLite)
```

## Project layout

```
config/            settings, urls, wsgi
apps/accounts      User (email login, roles), Student/Trainer profiles, login + rate limit
apps/core          SiteSettings, AuditLog, private storage, upload validators, public pages
apps/courses       Category, Course, Module, Lesson, VideoAsset, LessonResource, Batch, LiveClass,
                   LessonProgress, Assignment, Submission, Certificate + access rules & services
apps/enrollments   Enrollment, Payment, UpiPaymentSubmission, Razorpay/UPI services
apps/notifications Announcement, Notification + de-duplicated delivery
apps/dashboard     Admin dashboard (generic CRUD layer + custom screens)
apps/portal        Student portal, protected streaming/downloads, JSON API (DRF)
templates/ static/ Bootstrap 5 UI (navy / white / cyan)
docker/ nginx/ scripts/  entrypoint, reverse proxy, backup/restore/migrate/test scripts
tests/             automated test suite
```
