# syntax=docker/dockerfile:1

# =============================================================================
# XaninFarm application image
#
# Three stages:
#   1. assets  - node builds Tailwind into static/css/app.css
#   2. deps    - python wheels built once, with the compilers they need
#   3. runtime - slim image, no compilers, no node, runs as a non-root user
#
# The split matters: the final image never carries build-essential or the node
# toolchain, and a change to Python code does not invalidate the wheel cache.
# =============================================================================


# -----------------------------------------------------------------------------
# 1. Front-end assets
# -----------------------------------------------------------------------------
FROM node:22-slim AS assets

WORKDIR /build

# Only the manifest first, so `npm ci` is re-run when dependencies change and
# not every time a template is edited.
COPY package.json package-lock.json* ./
RUN npm install --no-audit --no-fund

# Tailwind scans templates and JS to decide which classes to emit, so the
# config and every source it globs must be present before `npm run build`.
COPY tailwind.config.js ./
COPY assets/ ./assets/
COPY templates/ ./templates/
COPY static/ ./static/
COPY apps/ ./apps/

RUN npm run build


# -----------------------------------------------------------------------------
# 2. Python dependencies
# -----------------------------------------------------------------------------
FROM python:3.12-slim AS deps

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

# libpq-dev and gcc are needed to build psycopg and Pillow; they stay in this
# stage and never reach the runtime image.
RUN apt-get update && apt-get install --no-install-recommends -y \
        build-essential \
        libpq-dev \
        libjpeg-dev \
        zlib1g-dev \
        libwebp-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /wheels
COPY requirements/ ./requirements/

# Build wheels for prod by default. `--build-arg REQUIREMENTS=dev` produces an
# image with pytest, ruff and the debug toolbar for CI and local work.
ARG REQUIREMENTS=prod
RUN pip wheel --wheel-dir /wheels/dist -r requirements/${REQUIREMENTS}.txt


# -----------------------------------------------------------------------------
# 3. Runtime
# -----------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    DJANGO_SETTINGS_MODULE=config.settings.prod

# Runtime libraries only - the -dev headers were build-time concerns.
# postgresql-client supplies pg_isready and pg_dump, used by the entrypoint
# and the backup documentation.
RUN apt-get update && apt-get install --no-install-recommends -y \
        libpq5 \
        libjpeg62-turbo \
        libwebp7 \
        postgresql-client \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Run as a non-root user. Created before the code is copied so ownership can be
# set in a single COPY rather than a costly recursive chown layer.
RUN groupadd --system --gid 1001 farm \
    && useradd --system --uid 1001 --gid farm --create-home --shell /bin/bash farm

WORKDIR /app

ARG REQUIREMENTS=prod
COPY --from=deps /wheels/dist /wheels/dist
COPY requirements/ ./requirements/
RUN pip install --no-index --find-links=/wheels/dist -r requirements/${REQUIREMENTS}.txt \
    && rm -rf /wheels

COPY --chown=farm:farm . .

# The compiled stylesheet from the node stage replaces whatever the build
# context happened to contain, so the image never ships a stale app.css.
COPY --from=assets --chown=farm:farm /build/static/css/app.css ./static/css/app.css

COPY --chown=farm:farm docker/entrypoint.sh /usr/local/bin/entrypoint.sh
# The sed strips carriage returns. This project is developed on Windows, and a
# script checked out with CRLF endings fails in the container with an
# "exec format error" that gives no hint about why.
RUN sed -i 's/\r$//' /usr/local/bin/entrypoint.sh \
    && chmod +x /usr/local/bin/entrypoint.sh

# Writable at runtime: collectstatic writes here, and uploads land in media.
RUN mkdir -p /app/staticfiles /app/mediafiles \
    && chown -R farm:farm /app/staticfiles /app/mediafiles

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
