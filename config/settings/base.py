"""
Base settings shared by every environment.

Environment-specific modules (dev / prod / test) import from here and override.
Every secret and deployment-specific value is read from the environment.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import environ

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
# config/settings/base.py -> config/settings -> config -> <project root>
BASE_DIR = Path(__file__).resolve().parents[2]
APPS_DIR = BASE_DIR / "apps"

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------
env = environ.Env()

# Read .env when present. In containers the variables are usually injected
# directly, so a missing file is not an error.
_env_file = BASE_DIR / ".env"
if _env_file.is_file():
    env.read_env(str(_env_file))

SECRET_KEY = env("DJANGO_SECRET_KEY", default="insecure-dev-key-override-me")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

SITE_NAME = env("SITE_NAME", default="XaninFarm")
SITE_DOMAIN = env("SITE_DOMAIN", default="xaninfarms.com")
SITE_BASE_URL = env("SITE_BASE_URL", default="http://localhost:8000").rstrip("/")
SITE_TAGLINE = "Fresh From Our Farm, Delivered to Your Door."

# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------
DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "django.contrib.sitemaps",
]

THIRD_PARTY_APPS = [
    "rest_framework",
    "rest_framework.authtoken",
    "django_filters",
    "django_htmx",
    "widget_tweaks",
    "django_celery_beat",
]

LOCAL_APPS = [
    "apps.core",
    "apps.accounts",
    "apps.products",
    "apps.inventory",
    "apps.cart",
    "apps.coupons",
    "apps.delivery",
    "apps.orders",
    "apps.payments",
    "apps.reviews",
    "apps.notifications",
    "apps.analytics",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.site_context",
                "apps.cart.context_processors.cart_context",
            ],
        },
    },
]

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
if env("DATABASE_URL", default=""):
    DATABASES = {"default": env.db("DATABASE_URL")}
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": env("POSTGRES_DB", default="xaninfarm"),
            "USER": env("POSTGRES_USER", default="xaninfarm"),
            "PASSWORD": env("POSTGRES_PASSWORD", default="xaninfarm"),
            "HOST": env("POSTGRES_HOST", default="localhost"),
            "PORT": env("POSTGRES_PORT", default="5432"),
        }
    }

DATABASES["default"]["CONN_MAX_AGE"] = env.int("DB_CONN_MAX_AGE", default=60)
DATABASES["default"]["ATOMIC_REQUESTS"] = False
if env.bool("DB_SSL_REQUIRE", default=False):
    DATABASES["default"].setdefault("OPTIONS", {})["sslmode"] = "require"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# Cache / sessions
# ---------------------------------------------------------------------------
REDIS_URL = env("REDIS_URL", default="redis://localhost:6379/0")

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
        "KEY_PREFIX": "xaninfarm",
    }
}

SESSION_ENGINE = "django.contrib.sessions.backends.cached_db"
SESSION_COOKIE_NAME = "xaninfarm_session"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = 60 * 60 * 24 * 14  # two weeks
CSRF_COOKIE_HTTPONLY = False  # HTMX reads the token from the cookie when needed
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_TRUSTED_ORIGINS = env.list("DJANGO_CSRF_TRUSTED_ORIGINS", default=[])

# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------
AUTH_USER_MODEL = "accounts.User"

AUTHENTICATION_BACKENDS = ["apps.accounts.backends.EmailOrPhoneBackend"]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 8},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

PASSWORD_RESET_TIMEOUT = 60 * 60 * 3  # 3 hours

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "core:home"
LOGOUT_REDIRECT_URL = "core:home"

# ---------------------------------------------------------------------------
# Internationalisation
# ---------------------------------------------------------------------------
LANGUAGE_CODE = "en-us"
TIME_ZONE = env("DJANGO_TIME_ZONE", default="Asia/Dhaka")
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------------------
# Static & media
# ---------------------------------------------------------------------------
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "mediafiles"

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

# Uploads: cap size and restrict types (validated in apps.core.validators).
DATA_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024  # 5 MB
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
MAX_UPLOAD_IMAGE_SIZE = 5 * 1024 * 1024
ALLOWED_IMAGE_EXTENSIONS = ["jpg", "jpeg", "png", "webp", "avif"]

# Tailwind is compiled to static/css/app.css by the node build stage. In local
# development without a node toolchain, fall back to the Play CDN.
TAILWIND_CDN_FALLBACK = env.bool("TAILWIND_CDN_FALLBACK", default=False)

# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------
EMAIL_BACKEND = env("EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = env("EMAIL_HOST", default="localhost")
EMAIL_PORT = env.int("EMAIL_PORT", default=1025)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=False)
EMAIL_TIMEOUT = 10
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="XaninFarm <no-reply@xaninfarms.com>")
SERVER_EMAIL = DEFAULT_FROM_EMAIL
ORDER_NOTIFICATION_EMAIL = env("ORDER_NOTIFICATION_EMAIL", default="orders@xaninfarms.com")
# Who receives the low-stock and expiry digests. Empty means "every active
# staff account", which is the right default for a small team.
OPS_EMAIL_RECIPIENTS = env.list("OPS_EMAIL_RECIPIENTS", default=[])

# Emails are rendered without a request, so links need an absolute base.
SITE_URL = env("SITE_URL", default="http://localhost:8000").rstrip("/")

# ---------------------------------------------------------------------------
# Celery
# ---------------------------------------------------------------------------
CELERY_BROKER_URL = env("CELERY_BROKER_URL", default=REDIS_URL)
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", default=REDIS_URL)
CELERY_TASK_ALWAYS_EAGER = env.bool("CELERY_TASK_ALWAYS_EAGER", default=False)
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_TIME_LIMIT = 5 * 60
CELERY_TASK_SOFT_TIME_LIMIT = 4 * 60
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"

# ---------------------------------------------------------------------------
# REST framework
# ---------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
        "rest_framework.authentication.TokenAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticatedOrReadOnly"],
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 24,
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {"anon": "60/min", "user": "240/min"},
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
}

# ---------------------------------------------------------------------------
# Storefront business rules
# ---------------------------------------------------------------------------
CURRENCY_CODE = env("CURRENCY_CODE", default="BDT")
CURRENCY_SYMBOL = "৳"  # BDT taka sign
ORDER_NUMBER_PREFIX = env("ORDER_NUMBER_PREFIX", default="XF")

DEFAULT_DELIVERY_FEE = Decimal(env("DEFAULT_DELIVERY_FEE", default="60.00"))
FREE_DELIVERY_THRESHOLD = Decimal(env("FREE_DELIVERY_THRESHOLD", default="1500.00"))

LOW_STOCK_THRESHOLD = env.int("LOW_STOCK_THRESHOLD", default=10)
EXPIRY_ALERT_DAYS = env.int("EXPIRY_ALERT_DAYS", default=3)
STOCK_RESERVATION_MINUTES = env.int("STOCK_RESERVATION_MINUTES", default=45)

# Products may not be sold if they expire within this many days of dispatch.
MIN_SHELF_LIFE_ON_SALE_DAYS = env.int("MIN_SHELF_LIFE_ON_SALE_DAYS", default=0)

CART_MAX_QUANTITY_PER_ITEM = 50

# Reviews wait for a moderator unless this is switched on.
REVIEW_AUTO_APPROVE = env.bool("REVIEW_AUTO_APPROVE", default=False)

# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------
PAYMENT_SANDBOX = env.bool("PAYMENT_SANDBOX", default=True)
ENABLED_PAYMENT_PROVIDERS = env.list("ENABLED_PAYMENT_PROVIDERS", default=["cod", "mock"])

PAYMENT_PROVIDERS = {
    "mock": {"secret": env("MOCK_PAYMENT_SECRET", default="mock-sandbox-secret")},
    "bkash": {
        "base_url": env("BKASH_BASE_URL", default=""),
        "app_key": env("BKASH_APP_KEY", default=""),
        "app_secret": env("BKASH_APP_SECRET", default=""),
        "username": env("BKASH_USERNAME", default=""),
        "password": env("BKASH_PASSWORD", default=""),
        "webhook_secret": env("BKASH_WEBHOOK_SECRET", default=""),
    },
    "nagad": {
        "base_url": env("NAGAD_BASE_URL", default=""),
        "merchant_id": env("NAGAD_MERCHANT_ID", default=""),
        "merchant_private_key": env("NAGAD_MERCHANT_PRIVATE_KEY", default=""),
        "pg_public_key": env("NAGAD_PG_PUBLIC_KEY", default=""),
        "webhook_secret": env("NAGAD_WEBHOOK_SECRET", default=""),
    },
    "sslcommerz": {
        "store_id": env("SSLCOMMERZ_STORE_ID", default=""),
        "store_password": env("SSLCOMMERZ_STORE_PASSWORD", default=""),
        "sandbox": env.bool("SSLCOMMERZ_SANDBOX", default=True),
        "webhook_secret": env("SSLCOMMERZ_WEBHOOK_SECRET", default=""),
    },
}

# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------
from django.contrib.messages import constants as message_constants  # noqa: E402

MESSAGE_TAGS = {
    message_constants.DEBUG: "debug",
    message_constants.INFO: "info",
    message_constants.SUCCESS: "success",
    message_constants.WARNING: "warning",
    message_constants.ERROR: "error",
}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{levelname} {asctime} {name} {process:d} {message}",
            "style": "{",
        },
        "simple": {"format": "{levelname} {message}", "style": "{"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "django": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "django.request": {"handlers": ["console"], "level": "ERROR", "propagate": False},
        "apps": {"handlers": ["console"], "level": "INFO", "propagate": False},
        # Payment and stock activity is auditable - keep it noisy.
        "apps.payments": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "apps.inventory": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}
