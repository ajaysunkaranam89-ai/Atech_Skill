#!/usr/bin/env bash
# Restore a backup created by scripts/backup.sh.
# Usage: ./scripts/restore.sh backups/20261109_020000
# WARNING: replaces the current database contents and media files.
set -euo pipefail
cd "$(dirname "$0")/.."

SRC="${1:?Usage: $0 backups/<timestamp>}"
[ -f "$SRC/database.dump" ] || { echo "Missing $SRC/database.dump"; exit 1; }
set -a; [ -f .env ] && . ./.env; set +a

( cd "$SRC" && sha256sum -c SHA256SUMS )

read -r -p "This will OVERWRITE database '$POSTGRES_DB' and all media. Type RESTORE to continue: " ok
[ "$ok" = "RESTORE" ] || { echo "Aborted."; exit 1; }

echo "==> Stopping web and nginx"
docker compose stop nginx web

echo "==> Restoring database"
docker compose exec -T db psql -U "$POSTGRES_USER" -d postgres -v ON_ERROR_STOP=1 \
  -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='${POSTGRES_DB}' AND pid <> pg_backend_pid();" \
  -c "DROP DATABASE IF EXISTS \"${POSTGRES_DB}\";" \
  -c "CREATE DATABASE \"${POSTGRES_DB}\" OWNER \"${POSTGRES_USER}\";"
docker compose exec -T db pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner < "$SRC/database.dump"

echo "==> Restoring media"
docker compose run --rm --no-deps -T --entrypoint sh web -c "rm -rf /app/media/* /app/private_media/* && tar xzf - -C /app" < "$SRC/media.tar.gz"

echo "==> Starting services (migrations run automatically)"
docker compose up -d
echo "==> Restore complete."
