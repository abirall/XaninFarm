"""Test settings: fast hashing, eager tasks, local-memory cache."""

from decimal import Decimal

from .base import *  # noqa: F401,F403
from .base import env

DEBUG = False
ALLOWED_HOSTS = ["*"]

SECRET_KEY = "test-only-secret-key"  # noqa: S105 - not used outside the test suite

# Fast password hashing keeps the auth tests quick.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
AUTH_PASSWORD_VALIDATORS = []

# Run Celery tasks inline so notification side effects are assertable.
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# Avoid a Redis dependency in CI; sessions go straight to the database.
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
SESSION_ENGINE = "django.contrib.sessions.backends.db"

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

TAILWIND_CDN_FALLBACK = True

# Deterministic money rules for assertions.
DEFAULT_DELIVERY_FEE = Decimal("60.00")
FREE_DELIVERY_THRESHOLD = Decimal("1500.00")

ENABLED_PAYMENT_PROVIDERS = ["cod", "mock"]
PAYMENT_SANDBOX = True

DATABASES["default"]["ATOMIC_REQUESTS"] = False  # noqa: F405
DATABASES["default"]["CONN_MAX_AGE"] = 0  # noqa: F405 - threads in concurrency tests

MEDIA_ROOT = env("TEST_MEDIA_ROOT", default="/tmp/xaninfarm-test-media")
