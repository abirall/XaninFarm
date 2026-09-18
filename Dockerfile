# syntax=docker/dockerfile:1
#
# XaninFarm application image.
#
#   1. assets  - node builds Tailwind into static/css/app.css
#   2. runtime - python image, no node toolchain, runs as a non-root user
#
# There is no compiler stage: psycopg[binary] and Pillow both ship prebuilt
# wheels, so pip never needs build-essential here.


# -----------------------------------------------------------------------------
# 1. Front-end assets
# -----------------------------------------------------------------------------
FROM node:22-slim AS assets

WORKDIR /build

# Manifest first, so npm only re-runs when dependencies change.
COPY package.json package-lock.json* ./
RUN npm install --no-audit --no-fund

# Tailwind scans these to decide which classes to emit.
COPY tailwind.config.js ./
COPY assets/ ./assets/
COPY templates/ ./templates/
COPY static/ ./static/
COPY apps/ ./apps/

RUN npm run build


# -----------------------------------------------------------------------------
# 2. Runtime
# -----------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    DJANGO_SETTINGS_MODULE=config.settings.prod

# postgresql-client supplies pg_isready and psql, used by the entrypoint.
RUN apt-get update && apt-get install --no-install-recommends -y \
    postgresql-client \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --system --create-home --uid 1001 farm

WORKDIR /app

# `--build-arg REQUIREMENTS=dev` adds pytest, ruff and the debug toolbar.
ARG REQUIREMENTS=prod
COPY requirements/ ./requirements/
RUN pip install -r requirements/${REQUIREMENTS}.txt

COPY --chown=farm:farm . .

# The compiled stylesheet replaces whatever the build context contained, so the
# image never ships a stale app.css.
COPY --from=assets --chown=farm:farm /build/static/css/app.css ./static/css/app.css

# Kept outside /app so the dev bind mount cannot shadow it. The sed strips
# carriage returns: this project is developed on Windows, and a script checked
# out with CRLF fails in the container with an "exec format error" that gives
# no hint about why.
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN sed -i 's/\r$//' /usr/local/bin/entrypoint.sh \
    && chmod +x /usr/local/bin/entrypoint.sh \
    && mkdir -p staticfiles mediafiles \
    && chown farm:farm staticfiles mediafiles

USER farm

EXPOSE 8000

# Hits the view that also checks the database, so an unhealthy DB marks the
# container unhealthy rather than letting it accept traffic it cannot serve.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl --fail --silent http://localhost:8000/healthz/ || exit 1

ENTRYPOINT ["entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", \
    "--bind", "0.0.0.0:8000", \
    "--workers", "3", \
    "--threads", "2", \
    "--timeout", "60", \
    "--access-logfile", "-", \
    "--error-logfile", "-"]
