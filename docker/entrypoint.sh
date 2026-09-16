#!/usr/bin/env bash
#
# Container entrypoint.
#
# Waits for the services this process actually needs, then runs the startup
# work guarded by environment flags. Migrations and collectstatic are opt-in
# rather than automatic: when several web containers start at once you want
# exactly one of them migrating, not all of them racing.
#
set -euo pipefail

log() { printf '[entrypoint] %s\n' "$*"; }

# -----------------------------------------------------------------------------
# Wait for PostgreSQL
# -----------------------------------------------------------------------------
wait_for_postgres() {
    local host="${POSTGRES_HOST:-db}"
    local port="${POSTGRES_PORT:-5432}"
    local user="${POSTGRES_USER:-xaninfarm}"
    local attempts="${WAIT_FOR_DB_ATTEMPTS:-60}"

    log "waiting for postgres at ${host}:${port}"
    for ((i = 1; i <= attempts; i++)); do
        if pg_isready --host="$host" --port="$port" --username="$user" --quiet; then
            log "postgres is ready"
            return 0
        fi
        sleep 1
    done

    log "ERROR: postgres did not become ready after ${attempts}s"
    return 1
}

# Redis is checked in Python rather than with redis-cli, which is not installed.
wait_for_redis() {
    local attempts="${WAIT_FOR_REDIS_ATTEMPTS:-30}"

    log "waiting for redis"
    for ((i = 1; i <= attempts; i++)); do
        if python - <<'PY' 2>/dev/null
import os, sys
import redis
url = os.environ.get("REDIS_URL", "redis://redis:6379/0")
try:
    redis.Redis.from_url(url, socket_connect_timeout=2).ping()
except Exception:
    sys.exit(1)
PY
        then
            log "redis is ready"
            return 0
        fi
        sleep 1
    done

    log "ERROR: redis did not become ready after ${attempts}s"
    return 1
}

# -----------------------------------------------------------------------------
# Startup work
# -----------------------------------------------------------------------------
if [ "${WAIT_FOR_DB:-1}" = "1" ]; then
    wait_for_postgres
fi

if [ "${WAIT_FOR_REDIS:-0}" = "1" ]; then
    wait_for_redis
fi

# Off by default. Set RUN_MIGRATIONS=1 on exactly one container, or run
# `docker compose run --rm web python manage.py migrate` as a deploy step.
if [ "${RUN_MIGRATIONS:-0}" = "1" ]; then
    log "applying migrations"
    python manage.py migrate --noinput
fi

# Whitenoise serves from STATIC_ROOT, so this must have run before the first
# request. Skipped when a build step or a shared volume already populated it.
if [ "${RUN_COLLECTSTATIC:-0}" = "1" ]; then
    log "collecting static files"
    python manage.py collectstatic --noinput --clear
fi

# Convenience for first boot only. Django refuses to create a duplicate, so
# repeated starts are harmless, and it is a no-op unless the password is set.
if [ "${CREATE_SUPERUSER:-0}" = "1" ] && [ -n "${DJANGO_SUPERUSER_PASSWORD:-}" ]; then
    log "ensuring superuser exists"
    python manage.py createsuperuser --noinput || log "superuser already present"
fi

log "starting: $*"
exec "$@"
