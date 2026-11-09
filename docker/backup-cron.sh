#!/bin/sh
# Daily backup service (runs in the `backup` container, image postgres:16-alpine).
# Every day at BACKUP_HOUR (container time zone, TZ) it writes /backups/<timestamp>/ with
# database.dump (pg_dump custom format), media.tar.gz (public + private uploads) and SHA256SUMS,
# deletes backups older than BACKUP_KEEP_DAYS, and records the result in /backups/last.json,
# which the admin dashboard reads to warn if backups stop working.
set -u
BACKUP_HOUR="${BACKUP_HOUR:-2}"
BACKUP_KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"
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

run_backup() {
  stamp=$(date +%Y%m%d_%H%M%S)
  dir="/backups/$stamp"
  mkdir -p "$dir"
  echo "[$(date)] Starting backup $stamp"
  if ! PGPASSWORD="$POSTGRES_PASSWORD" pg_dump -h db -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc --no-owner -f "$dir/database.dump"; then
    write_status false "$dir" "pg_dump failed"
    echo "[$(date)] ERROR: pg_dump failed"
    return 1
  fi
  if ! tar czf "$dir/media.tar.gz" -C /data media private_media; then
    write_status false "$dir" "media archive failed"
    echo "[$(date)] ERROR: media archive failed"
    return 1
  fi
  (cd "$dir" && sha256sum database.dump media.tar.gz > SHA256SUMS)
  chmod -R go-rwx "$dir"
  write_status true "$dir" "ok"
  find /backups -mindepth 1 -maxdepth 1 -type d -mtime +"$BACKUP_KEEP_DAYS" -exec rm -rf {} +
  echo "[$(date)] Backup $stamp complete ($(du -sh "$dir" | cut -f1))"
}

if [ "${BACKUP_ON_START:-0}" = "1" ]; then
  run_backup && date +%F > /backups/.last_run_date
fi

while true; do
  if [ "$(date +%H)" = "$HOUR" ] && [ "$(cat /backups/.last_run_date 2>/dev/null)" != "$(date +%F)" ]; then
    run_backup && date +%F > /backups/.last_run_date
  fi
  sleep 60
done
