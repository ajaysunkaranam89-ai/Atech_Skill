#!/usr/bin/env bash
# Apply database migrations manually (they also run automatically on every container start).
# Usage: ./scripts/migrate.sh            -> migrate
#        ./scripts/migrate.sh --plan     -> show pending migrations without applying
set -euo pipefail
cd "$(dirname "$0")/.."
if [ "${1:-}" = "--plan" ]; then
  docker compose exec -T web python manage.py migrate --plan
else
  docker compose exec -T web python manage.py migrate --noinput
  docker compose exec -T web python manage.py createcachetable
fi
