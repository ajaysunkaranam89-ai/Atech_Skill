# Production deployment — Ubuntu 22.04/24.04 and Rocky Linux 9

The stack runs 3 containers: `db` (PostgreSQL 16, persistent volume `pg_data`), `web` (Django + Gunicorn), and `nginx` (reverse proxy, static, public media, protected file delivery).

Named volumes: `pg_data`, `static_data`, `media_data` (thumbnails/logo, public), `private_media` (videos, PDFs, submissions, UPI screenshots — never public).

## 1. Server requirements
- 2 vCPU / 4 GB RAM minimum (more if you host many uploaded MP4s); disk sized for your video library + backups.
- A domain name pointing (A record) to the server.
- Ports 80 and 443 open.

## 2. Install Docker

### Ubuntu
```bash
sudo apt-get update && sudo apt-get install -y ca-certificates curl git
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" | sudo tee /etc/apt/sources.list.d/docker.list
sudo apt-get update && sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo systemctl enable --now docker
sudo usermod -aG docker $USER   # log out/in afterwards
sudo ufw allow OpenSSH && sudo ufw allow 80,443/tcp && sudo ufw enable
```

### Rocky Linux 9
```bash
sudo dnf -y install dnf-plugins-core git
sudo dnf config-manager --add-repo https://download.docker.com/linux/rhel/docker-ce.repo
sudo dnf -y install docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo systemctl enable --now docker
sudo usermod -aG docker $USER   # log out/in afterwards
sudo firewall-cmd --permanent --add-service=http --add-service=https && sudo firewall-cmd --reload
```
SELinux (enforcing) note: the compose file bind-mounts `./nginx/...` read-only. If Nginx cannot read them, relabel once:
```bash
sudo chcon -Rt container_file_t ./nginx
```

## 3. Get the code and configure
```bash
git clone https://github.com/ajaysunkaranam89-ai/Atech_Skill.git techskills && cd techskills
cp .env.example .env
python3 -c "import secrets;print(secrets.token_urlsafe(50))"   # paste as DJANGO_SECRET_KEY
nano .env
```
Set at least: `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`, `SITE_URL`, `DJANGO_SUPERADMIN_EMAIL/PASSWORD`, `POSTGRES_PASSWORD`, SMTP settings, Razorpay keys. Keep `HTTPS_ENABLED=0` until §6 is done.
```bash
chmod 600 .env
chmod +x scripts/*.sh docker/entrypoint.sh
```

## 4. Start
```bash
docker compose up -d --build
docker compose ps            # all three should become "healthy"
docker compose logs -f web   # migrations, collectstatic, superadmin creation
```
Every start of `web` runs: wait for DB → `migrate` → `createcachetable` → `ensure_superadmin` (only if missing) → `collectstatic` → Gunicorn.

## 5. First login
Open `http://your-domain/accounts/login/`, log in as the Super Admin, **change the password** (top-right menu), then go to **Website settings** and fill in contact details, UPI ID/QR and social links.

## 6. HTTPS with Let's Encrypt
```bash
mkdir -p nginx/certs nginx/certbot-www
docker run --rm -v "$PWD/nginx/certs:/etc/letsencrypt" -v "$PWD/nginx/certbot-www:/var/www/certbot" \
  certbot/certbot certonly --webroot -w /var/www/certbot -d atechskills.in -d www.atechskills.in \
  --email you@example.com --agree-tos --no-eff-email
cp nginx/ssl.conf.example nginx/conf.d/default.conf
sed -i 's/atechskills.in/YOUR_DOMAIN/g' nginx/conf.d/default.conf
```
In `.env`: `HTTPS_ENABLED=1`, `SITE_URL=https://YOUR_DOMAIN`, `DJANGO_CSRF_TRUSTED_ORIGINS=https://YOUR_DOMAIN`, then:
```bash
docker compose up -d
```
Renewal (cron, e.g. `/etc/cron.d/certbot-techskills`):
```
0 3 * * 1 root cd /opt/techskills && docker run --rm -v "$PWD/nginx/certs:/etc/letsencrypt" -v "$PWD/nginx/certbot-www:/var/www/certbot" certbot/certbot renew --quiet && docker compose exec nginx nginx -s reload
```

## 7. Razorpay
1. Razorpay Dashboard → Settings → API Keys → copy Key ID / Secret into `.env`.
2. Settings → Webhooks → add `https://YOUR_DOMAIN/payments/razorpay/webhook/`, events `payment.captured`, `payment.failed`, `order.paid`; set a secret and copy it to `RAZORPAY_WEBHOOK_SECRET`.
3. Test with `rzp_test_` keys before switching to live keys. `docker compose up -d` after changing `.env`.

The browser never decides the amount: orders are created server-side from the course price, the checkout signature is verified with HMAC-SHA256, and the webhook body signature and amount are verified before enrollment is activated. Duplicate deliveries are harmless.

## 8. Video hosting recommendations
| Option | Access control | Notes |
|---|---|---|
| YouTube unlisted | **None** (anyone with link) | Free, good for previews / low-risk content |
| Uploaded MP4 (this app) | Enrollment check on every request, Nginx internal delivery | Good default; link cannot be shared to non-enrolled users, but enrolled users can still screen-record |
| Protected video service (Bunny Stream, Mux, Cloudflare Stream, VdoCipher) | Signed expiring URLs, DRM, watermarking | Recommended for premium paid content; use the *External HTTPS URL* source |

For large libraries, move `private_media` to S3-compatible storage (django-storages) with signed URLs. Storage is isolated in `apps/core/models.py::PrivateStorage`.

## 9. Backups and restore
```bash
./scripts/backup.sh                     # -> backups/<timestamp>/database.dump + media.tar.gz + SHA256SUMS
KEEP_DAYS=14 ./scripts/backup.sh        # also prunes old backups
./scripts/restore.sh backups/20261109_020000   # asks you to type RESTORE
```
Daily cron:
```
30 2 * * * root cd /opt/techskills && ./scripts/backup.sh >> /var/log/techskills-backup.log 2>&1
```
Copy `backups/` off the server (rclone/S3/another host). Test a restore on a staging server periodically.

## 10. Updates and migrations
```bash
git pull
docker compose up -d --build          # migrations run automatically on start
./scripts/migrate.sh --plan           # optional: inspect pending migrations
```
Take a backup before every update.

## 11. Health checks and logs
- `GET /healthz/` → `{"status":"ok","database":"ok"}` (used by the `web` container healthcheck)
- `GET /nginx-health` → `ok` (Nginx healthcheck)
- `docker compose logs -f web nginx db`

## 12. Security checklist
- [ ] `DJANGO_DEBUG=0`, strong unique `DJANGO_SECRET_KEY` and `POSTGRES_PASSWORD`, `.env` mode 600
- [ ] HTTPS enabled (`HTTPS_ENABLED=1`) → secure cookies, HSTS, SSL redirect
- [ ] Super Admin password changed after first login; staff accounts use unique passwords
- [ ] PostgreSQL not exposed publicly (compose publishes only Nginx ports)
- [ ] Off-site backups configured and a restore tested
- [ ] Razorpay webhook secret configured; live keys only on production
- [ ] Review **Audit log** periodically (logins, failed logins, deletions, payments, exports)
- [ ] Optional: a CSP header in Nginx once you know every third-party origin you use (YouTube, Razorpay, jsDelivr, Google Fonts)

## 13. Security & reliability features
- **2-step login (staff):** with `STAFF_2FA_REQUIRED=1` every admin, course admin and trainer must scan a QR code with an authenticator app (Google/Microsoft Authenticator, Authy) before using the dashboard. Each gets 8 one-time recovery codes. A Super Admin can reset a lost phone from **Administrators → Reset 2FA**. `/django-admin/` is disabled unless `DJANGO_ADMIN_ENABLED=1`.
- **Email queue:** all emails are stored in the database and delivered by the `worker` container (retries after 1, 5, 15, 60, 240 min). See **Email delivery** in the dashboard; check `docker compose logs worker`.
- **Error alerts:** set `DJANGO_ADMINS="Name <email>"` to receive error reports by email and/or `SENTRY_DSN` for Sentry (no personal data is sent).
- **Nightly backups:** the `backup` container writes `./backups/<timestamp>/` at `BACKUP_HOUR` (default 02:00) and keeps `BACKUP_KEEP_DAYS` (default 14). The dashboard shows the last backup and turns red if it fails or is older than 30 h. Copy `./backups` off the server daily, e.g. `rclone sync ./backups remote:techskills-backups`. Restore with `./scripts/restore.sh backups/<timestamp>`.
