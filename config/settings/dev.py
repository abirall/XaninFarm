"""Local development settings."""

from .base import *  # noqa: F401,F403
from .base import BASE_DIR, INSTALLED_APPS, MIDDLEWARE, env

DEBUG = env.bool("DJANGO_DEBUG", default=True)

ALLOWED_HOSTS = env.list(
    "DJANGO_ALLOWED_HOSTS",
    default=["localhost", "127.0.0.1", "0.0.0.0", "[::1]", "web"],
)

# Never use the manifest storage in development - it requires collectstatic.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

# Serve the Tailwind Play CDN when static/css/app.css has not been built yet.
TAILWIND_CDN_FALLBACK = env.bool(
    "TAILWIND_CDN_FALLBACK",
    default=not (BASE_DIR / "static" / "css" / "app.css").is_file(),
)

EMAIL_BACKEND = env(
    "EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend"
)

# Relaxed password rules keep local fixtures and demo seeding usable.
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 8},
    },
]

# --- django-debug-toolbar (optional; skipped when not installed) ---
try:
    import debug_toolbar  # noqa: F401
except ImportError:
    pass
else:
    INSTALLED_APPS = [*INSTALLED_APPS, "debug_toolbar"]
    MIDDLEWARE = [
        "debug_toolbar.middleware.DebugToolbarMiddleware",
        *MIDDLEWARE,
    ]
    INTERNAL_IPS = ["127.0.0.1", "localhost"]
    # Docker assigns dynamic gateway IPs, so allow the toolbar by callback.
    DEBUG_TOOLBAR_CONFIG = {"SHOW_TOOLBAR_CALLBACK": lambda request: DEBUG}
