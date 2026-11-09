#!/usr/bin/env bash
# Back up PostgreSQL + uploaded media (public and private) to ./backups/<timestamp>/
# Usage: ./scripts/backup.sh            (run from the project directory)
#        KEEP_DAYS=14 ./scripts/backup.sh   (delete backups older than 14 days, default 30)
set -euo pipefail
cd "$(dirname "$0")/.."
export MSYS_NO_PATHCONV=1  # Git Bash on Windows: do not rewrite container paths like /app

# Read only the values we need (.env is not valid shell syntax, e.g. "Name <email>" values).
envget() { [ -f .env ] && grep -E "^$1=" .env | tail -1 | cut -d= -f2- | tr -d '\r'; }
POSTGRES_DB=$(envget POSTGRES_DB); POSTGRES_USER=$(envget POSTGRES_USER)
STAMP=$(date +%Y%m%d_%H%M%S)
DEST="backups/${STAMP}"
KEEP_DAYS="${KEEP_DAYS:-30}"
mkdir -p "$DEST"

echo "==> Dumping database ${POSTGRES_DB}"
docker compose exec -T db pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc --no-owner > "$DEST/database.dump"

echo "==> Archiving media (thumbnails) and private media (videos, PDFs, submissions)"
docker compose exec -T web tar czf - -C /app media private_media > "$DEST/media.tar.gz"

( cd "$DEST" && sha256sum database.dump media.tar.gz > SHA256SUMS )
echo "==> Backup written to $DEST"
du -sh "$DEST"

if [ "$KEEP_DAYS" -gt 0 ]; then
  find backups -mindepth 1 -maxdepth 1 -type d -mtime +"$KEEP_DAYS" -print -exec rm -rf {} +
fi
