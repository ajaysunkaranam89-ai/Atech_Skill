# Admin guide — running the academy from the dashboard

Everything below is done at **/dashboard/**. You never need to edit code or the database.

## Add a course
1. **Course management → Add course.** Fill in title, descriptions, thumbnail, price and discounted price, duration, level, syllabus (one topic per line), objectives, prerequisites, trainers, and access days (0 = lifetime).
2. Save as **Draft**. You are taken to the **Course builder**.
3. **Add module** for each section. In the builder, drag the ⋮⋮ handle to reorder modules or lessons. Lessons can be dragged between modules, and the order saves automatically.
4. Click **Publish** when ready. Only *Published* courses appear on the public site. *Unpublish* hides a course from the catalogue, and *Archive* retires it. Enrolled students keep access to unpublished courses through **My Learning**.

## Add a recorded lesson
**Recorded videos → Add recorded video** (or **+ Lesson** on a module in the builder):
- Pick the course and module, then enter the lesson title and description.
- **Video source**: YouTube unlisted/watch URL, YouTube embed URL, external HTTPS URL, or **Upload MP4**. Choose *No video yet* for a text or PDF-only lesson.
- Optional: thumbnail, durations, PDF notes, order, and *Free preview* (visible to everyone).
- **Save as draft** or **Publish now**.
- **Notify students**: all enrolled, selected batches, or silently.

## Replace / update a video
Open the lesson's **Replace video** (🔁) action. The new upload becomes version *n+1* and is shown to students immediately. Old versions stay in **Versions** for audit and rollback. Use **Restore** (↺) to make an older version active again. Tick *Delete the previous uploaded MP4* to free disk space; the history record stays.

## Unpublish / delete
- **Unpublish** (eye-slash) hides a lesson from students instantly. Admins can still **Preview** it.
- **Delete** asks you to type the exact lesson or course name. Enrollment, payment and certificate records are always kept.

## Study materials
**PDF & study material → Add material**, or the 📄+ icon on a lesson in the builder. Untick *Downloadable* for view-in-class-only files. Replacing a file increments its version and removes the old file.

## Notifications
Every content form has a **Notify students** box. The same change is never notified twice, even if you click Save several times. A real new change (new video version, edited text, new file) notifies again. **Notifications → New announcement** sends general messages to everyone, a course, or a batch. **Delivery log** shows who received what.

## Batches & live classes
**Batch management → Add batch** (code, dates, schedule, capacity, trainer). Open a batch to add enrolled students. **Live classes → Schedule** with a Google Meet link. Batch students get a dashboard and email notification and see a *Join now* button 15 minutes before start.

## Payments & enrollments (Super Admin)
- Razorpay payments activate enrollment automatically after server-side verification.
- **Payments → UPI verification**: open each pending submission, check your bank/UPI app for the UTR and amount (mismatches are shown in red), then **Approve** or **Reject**.
- **Enrollments → Enroll a student** grants manual or complimentary access. **Cancel** removes access but keeps history.

## Students, certificates, reports, settings
- **Student management**: profiles, enrollments, progress, payments, submissions.
- **Certificates → Issue**: the student must be enrolled. The student gets a printable certificate and a public verification link `/certificates/verify/<number>/`.
- **Reports**: revenue by month, course performance, top lessons, and CSV exports.
- **Website settings**: name, tagline, hero text, logo, contact details, UPI ID/QR, payment toggles, email toggle.
- **Administrators**: create Course Administrators and Trainers (Super Admin only). Assign trainers to courses on the course form.
- **Audit log**: every create, update, delete, publish, login, payment approval and export.

## SEO & getting leads
1. **Website settings → SEO & analytics:** set the *Home page SEO title* and description, your *City*, the *Google Search Console* verification code and your *Google Analytics 4* ID (G-…).
2. **Google Search Console:** add your domain, verify with the code above, then submit `https://YOUR_DOMAIN/sitemap.xml`. Published courses, trainer pages and policy pages are listed automatically.
3. **Each course → Edit → SEO (Google search):** write an SEO title (≤60 chars, e.g. "CCNA Training in Bangalore | Live Online Classes") and description (≤155 chars). If left blank, the course title and short description are used.
4. **Course pages** carry Google "Course" rich-result data (price in INR, trainer, what you'll learn). Check with Google's Rich Results Test.
5. **Leads:** the home, course and contact pages have a *Book a free demo / call back* form, and a WhatsApp button appears once *WhatsApp number* is set. New leads are shown in **Enquiries (leads)** with a badge, and Super/Course Admins get a dashboard and email alert.
6. **Track campaigns:** add UTM tags to links you share, e.g. `https://YOUR_DOMAIN/courses/ccna/?utm_source=instagram&utm_medium=story&utm_campaign=ccna-dec`. Each lead shows where it came from; filter by source or export to CSV.
