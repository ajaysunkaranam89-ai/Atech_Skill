#!/usr/bin/env bash
# Run the automated test suite inside Docker against PostgreSQL.
# Usage: ./scripts/run_tests.sh
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose up -d db
docker compose run --rm --no-deps --entrypoint python web manage.py test tests --noinput -v 2
