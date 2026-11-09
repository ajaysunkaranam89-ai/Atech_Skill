#!/bin/sh
# Container start-up: wait for PostgreSQL, apply migrations, collect static, start Gunicorn.
set -e

echo "Waiting for PostgreSQL at ${POSTGRES_HOST:-db}:${POSTGRES_PORT:-5432}..."
python - <<'PY'
import os, sys, time
import psycopg
for i in range(60):
    try:
        psycopg.connect(
            host=os.environ.get("POSTGRES_HOST", "db"), port=os.environ.get("POSTGRES_PORT", "5432"),
            dbname=os.environ["POSTGRES_DB"], user=os.environ["POSTGRES_USER"],
            password=os.environ["POSTGRES_PASSWORD"], connect_timeout=3,
        ).close()
        print("PostgreSQL is ready.")
        sys.exit(0)
    except Exception as e:
        print(f"  not ready yet ({e.__class__.__name__}), retry {i + 1}/60")
        time.sleep(2)
sys.exit("PostgreSQL did not become ready in time")
PY

if [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
  python manage.py migrate --noinput
  python manage.py createcachetable
  python manage.py ensure_superadmin
fi
python manage.py collectstatic --noinput --verbosity 0

if [ "$1" = "gunicorn" ]; then
  exec gunicorn config.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers "${GUNICORN_WORKERS:-3}" \
    --threads "${GUNICORN_THREADS:-2}" \
    --timeout "${GUNICORN_TIMEOUT:-600}" \
    --graceful-timeout 30 \
    --max-requests 1000 --max-requests-jitter 100 \
    --forwarded-allow-ips="*" \
    --access-logfile - --error-logfile -
fi
exec "$@"
