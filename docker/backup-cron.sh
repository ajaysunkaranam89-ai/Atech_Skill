#!/bin/sh
# Daily backup service (runs in the `backup` container, image postgres:16-alpine).
# Every day at BACKUP_HOUR (container time zone, TZ) it writes /backups/<timestamp>/ with
# database.dump (pg_dump custom format), media.tar.gz (public + private uploads) and SHA256SUMS,
# deletes backups older than BACKUP_KEEP_DAYS, and records the result in /backups/last.json,
# which the admin dashboard reads to warn if backups stop working.
#
# Robustness:
#  * waits for PostgreSQL to accept connections before every backup (after a reboot all
#    containers start at once, so the database may not be ready yet);
#  * a failed backup is retried every RETRY_MINUTES (default 10), up to 6 times a day;
#  * catch-up: if the last successful backup is older than 24 h (e.g. the server was off
#    at BACKUP_HOUR), a backup runs as soon as the service starts.
set -u
BACKUP_HOUR="${BACKUP_HOUR:-2}"
BACKUP_KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"
RETRY_MINUTES="${RETRY_MINUTES:-10}"
HOUR=$(printf "%02d" "$BACKUP_HOUR")
mkdir -p /backups
# Folder + last.json readable by the web app (dashboard status); each backup dir is private (go-rwx below).
chmod 755 /backups 2>/dev/null || true
echo "Backup service: daily at ${HOUR}:00 (TZ=${TZ:-UTC}), keeping ${BACKUP_KEEP_DAYS} days."

write_status() {  # $1 ok(true|false) $2 dir $3 message
  size=$(du -sk "$2" 2>/dev/null | cut -f1)
  printf '{"finished": "%s", "ok": %s, "path": "%s", "size_kb": %s, "message": "%s"}\n' \
    "$(date -Iseconds)" "$1" "$(basename "$2")" "${size:-0}" "$3" > /backups/last.json.tmp
  chmod 644 /backups/last.json.tmp
  mv /backups/last.json.tmp /backups/last.json
}

wait_for_db() {
  i=0
  until pg_isready -h db -U "$POSTGRES_USER" -d "$POSTGRES_DB" -q; do
    i=$((i + 1))
    [ "$i" -ge 60 ] && return 1  # ~5 minutes
    sleep 5
  done
}

fail() {  # $1 dir $2 message
  write_status false "$1" "$2"
  rm -rf "$1"
  echo "[$(date)] ERROR: $2"
  return 1
}

run_backup() {
  stamp=$(date +%Y%m%d_%H%M%S)
  dir="/backups/$stamp"
  mkdir -p "$dir"
  echo "[$(date)] Starting backup $stamp"
  wait_for_db || { fail "$dir" "database not reachable"; return 1; }
  PGPASSWORD="$POSTGRES_PASSWORD" pg_dump -h db -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc --no-owner -f "$dir/database.dump" \
    || { fail "$dir" "pg_dump failed"; return 1; }
  tar czf "$dir/media.tar.gz" -C /data media private_media || { fail "$dir" "media archive failed"; return 1; }
  (cd "$dir" && sha256sum database.dump media.tar.gz > SHA256SUMS)
  chmod -R go-rwx "$dir"
  write_status true "$dir" "ok"
  date +%s > /backups/.last_success
  date +%F > /backups/.last_run_date
  find /backups -mindepth 1 -maxdepth 1 -type d -mtime +"$BACKUP_KEEP_DAYS" -exec rm -rf {} +
  echo "[$(date)] Backup $stamp complete ($(du -sh "$dir" | cut -f1))"
}

last_success_age() {
  last=$(cat /backups/.last_success 2>/dev/null || echo 0)
  echo $(( $(date +%s) - last ))
}

retries=0
next_retry=0
# Catch-up on start: forced by BACKUP_ON_START=1, or when the last good backup is >24 h old.
if [ "${BACKUP_ON_START:-0}" = "1" ] || [ "$(last_success_age)" -gt 86400 ]; then
  run_backup || next_retry=$(( $(date +%s) + RETRY_MINUTES * 60 ))
fi

while true; do
  now=$(date +%s)
  if [ "$(date +%H)" = "$HOUR" ] && [ "$(cat /backups/.last_run_date 2>/dev/null)" != "$(date +%F)" ] && [ "$next_retry" -eq 0 ]; then
    retries=0
    run_backup || next_retry=$(( now + RETRY_MINUTES * 60 ))
  elif [ "$next_retry" -gt 0 ] && [ "$now" -ge "$next_retry" ]; then
    retries=$((retries + 1))
    echo "[$(date)] Retrying failed backup (attempt $retries)"
    if run_backup; then
      next_retry=0; retries=0
    elif [ "$retries" -ge 6 ]; then
      echo "[$(date)] Giving up until the next scheduled run."; next_retry=0; retries=0
    else
      next_retry=$(( now + RETRY_MINUTES * 60 ))
    fi
  fi
  sleep 60
done
