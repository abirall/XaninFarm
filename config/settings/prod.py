"""Production settings. Fails fast when required secrets are missing."""

from __future__ import annotations

import logging

from .base import *  # noqa: F401,F403
from .base import LOGGING, env

# ---------------------------------------------------------------------------
# Hard requirements - refuse to boot insecurely.
# ---------------------------------------------------------------------------
DEBUG = False

SECRET_KEY = env("DJANGO_SECRET_KEY")  # raises ImproperlyConfigured when unset
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS")
CSRF_TRUSTED_ORIGINS = env.list("DJANGO_CSRF_TRUSTED_ORIGINS")

# ---------------------------------------------------------------------------
# HTTPS / cookies
# ---------------------------------------------------------------------------
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env.bool("DJANGO_SECURE_SSL_REDIRECT", default=True)

SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"

SECURE_HSTS_SECONDS = env.int("DJANGO_SECURE_HSTS_SECONDS", default=60 * 60 * 24 * 365)
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

# ---------------------------------------------------------------------------
# Email - real SMTP in production
# ---------------------------------------------------------------------------
EMAIL_BACKEND = env("EMAIL_BACKEND", default="django.core.mail.backends.smtp.EmailBackend")
EMAIL_HOST = env("EMAIL_HOST")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)

ADMINS = [("XaninFarm Ops", env("ORDER_NOTIFICATION_EMAIL", default="ops@xaninfarms.com"))]
MANAGERS = ADMINS

# ---------------------------------------------------------------------------
# Logging - warn-level console plus mail_admins for 500s
# ---------------------------------------------------------------------------
LOGGING = {
    **LOGGING,
    "handlers": {
        **LOGGING["handlers"],
        "mail_admins": {
            "class": "django.utils.log.AdminEmailHandler",
            "level": "ERROR",
            "include_html": False,
        },
    },
}
LOGGING["loggers"]["django.request"]["handlers"] = ["console", "mail_admins"]

# ---------------------------------------------------------------------------
# Sentry (optional)
# ---------------------------------------------------------------------------
SENTRY_DSN = env("SENTRY_DSN", default="")
if SENTRY_DSN:
    try:
        import sentry_sdk
        from sentry_sdk.integrations.celery import CeleryIntegration
        from sentry_sdk.integrations.django import DjangoIntegration
        from sentry_sdk.integrations.logging import LoggingIntegration
    except ImportError:  # pragma: no cover
        logging.getLogger(__name__).warning("SENTRY_DSN set but sentry-sdk is not installed")
    else:
        sentry_sdk.init(
            dsn=SENTRY_DSN,
            environment=env("SENTRY_ENVIRONMENT", default="production"),
            integrations=[
                DjangoIntegration(),
                CeleryIntegration(),
                LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
            ],
            traces_sample_rate=env.float("SENTRY_TRACES_SAMPLE_RATE", default=0.05),
            send_default_pii=False,
        )
