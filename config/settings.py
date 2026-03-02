"""
MCS Financial Statement Platform - Django Settings
"""
import os
from pathlib import Path
import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1"]),
)
environ.Env.read_env(os.path.join(BASE_DIR, ".env"))

SECRET_KEY = env("SECRET_KEY")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")

# Application definition
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    # Third-party
    "django_htmx",
    "crispy_forms",
    "crispy_bootstrap5",
    "csp",
    "django_celery_beat",
    # Local apps
    "accounts.apps.AccountsConfig",
    "core.apps.CoreConfig",
    "review.apps.ReviewConfig",
    "integrations.apps.IntegrationsConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "csp.middleware.CSPMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
    "config.middleware.Require2FAMiddleware",
]

ROOT_URLCONF = "config.urls"

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
                "config.context_processors.build_info",
                "core.context_processors.office_admin_context",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# Database - SQLite for dev, PostgreSQL for production
DATABASES = {
    "default": env.db("DATABASE_URL", default="sqlite:///db.sqlite3")
}
# Keep database connections alive for 10 minutes (avoids reconnect overhead
# under concurrent load — critical for 100+ simultaneous users)
DATABASES["default"]["CONN_MAX_AGE"] = int(os.environ.get("DB_CONN_MAX_AGE", 600))
DATABASES["default"]["CONN_HEALTH_CHECKS"] = True

# Custom user model
AUTH_USER_MODEL = "accounts.User"

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Internationalization
LANGUAGE_CODE = "en-au"
TIME_ZONE = "Australia/Melbourne"
USE_I18N = True
USE_TZ = True

# Static files
STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

# Media files (uploads, generated documents)
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

# Default primary key field type
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Crispy forms
CRISPY_ALLOWED_TEMPLATE_PACKS = "bootstrap5"
CRISPY_TEMPLATE_PACK = "bootstrap5"

# Login/logout redirects
LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/accounts/login/"

# Session settings
SESSION_COOKIE_AGE = 1800  # 30 minutes
SESSION_SAVE_EVERY_REQUEST = True  # Reset timeout on activity
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_SAMESITE = "Lax"

# CSRF trusted origins (configured via environment)
CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get(
        "CSRF_TRUSTED_ORIGINS",
        "https://statementhub.com.au,https://www.statementhub.com.au",
    ).split(",")
    if origin.strip()
]
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_FAILURE_VIEW = "config.middleware.csrf_failure_view"

# Email configuration
# Production: uses Resend HTTP API (bypasses SMTP port blocking)
# Development: console backend (emails printed to terminal)
# Set RESEND_API_KEY in .env to enable Resend, otherwise falls back to console
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
EMAIL_BACKEND = os.environ.get(
    "EMAIL_BACKEND",
    "config.email_backend.ResendEmailBackend" if RESEND_API_KEY else "django.core.mail.backends.console.EmailBackend",
)
DEFAULT_FROM_EMAIL = os.environ.get(
    "DEFAULT_FROM_EMAIL",
    "StatementHub <noreply@statementhub.com.au>",
)
# Legacy SMTP settings (kept for fallback if needed)
EMAIL_HOST = os.environ.get("EMAIL_HOST", "")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", 587))
EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "True").lower() == "true"
EMAIL_USE_SSL = os.environ.get("EMAIL_USE_SSL", "False").lower() == "true"
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")

# ─── Caching (for concurrent user performance) ───────────────────────────────
# Use database-backed cache in production; falls back to local memory for dev.
# For 100+ concurrent users, consider Redis: CACHE_URL=redis://localhost:6379/0
CACHES = {
    "default": {
        "BACKEND": os.environ.get(
            "CACHE_BACKEND",
            "django.core.cache.backends.locmem.LocMemCache",
        ),
        "LOCATION": os.environ.get("CACHE_LOCATION", "statementhub-cache"),
        "TIMEOUT": 300,
        "OPTIONS": {
            "MAX_ENTRIES": 1000,
        },
    }
}

# Use cached sessions for better performance under concurrent load
# (avoids a DB query per request for session data)
SESSION_ENGINE = os.environ.get(
    "SESSION_ENGINE",
    "django.contrib.sessions.backends.cached_db",
)

# Webhook secret for n8n integration (set in .env)
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")

# Security settings (enforced in production)
if not DEBUG:
    SECURE_BROWSER_XSS_FILTER = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_SSL_REDIRECT = True
    CSRF_COOKIE_SECURE = True
    X_FRAME_OPTIONS = "DENY"
    # HSTS — tell browsers to only use HTTPS for 1 year
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"

# File upload limits (prevent memory exhaustion)
FILE_UPLOAD_MAX_MEMORY_SIZE = 20 * 1024 * 1024  # 20MB
DATA_UPLOAD_MAX_MEMORY_SIZE = 20 * 1024 * 1024  # 20MB
DATA_UPLOAD_MAX_NUMBER_FIELDS = 5000

# Content Security Policy (via django-csp 4.x)
CONTENT_SECURITY_POLICY = {
    "DIRECTIVES": {
        "default-src": ("'self'",),
        "style-src": ("'self'", "'unsafe-inline'", "https://cdn.jsdelivr.net", "https://fonts.googleapis.com"),
        "script-src": ("'self'", "'unsafe-inline'", "https://cdn.jsdelivr.net", "https://unpkg.com"),
        "font-src": ("'self'", "https://cdn.jsdelivr.net", "https://fonts.gstatic.com"),
        "img-src": ("'self'", "data:"),
        "connect-src": ("'self'",),
    }
}

# Database SSL in production
if not DEBUG and "sqlite" not in DATABASES["default"].get("ENGINE", ""):
    DATABASES["default"].setdefault("OPTIONS", {})["sslmode"] = os.environ.get("DB_SSLMODE", "prefer")

# Logging — scrub PII (emails, TFNs, phone numbers) from all log output
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "sensitive_data": {
            "()": "config.log_filters.SensitiveDataFilter",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "filters": ["sensitive_data"],
        },
    },
    "root": {
        "handlers": ["console"],
        "level": os.environ.get("LOG_LEVEL", "INFO"),
    },
}

# MC&S Logo for financial statement documents
MCS_LOGO_PATH = BASE_DIR / "static" / "MCSlogo.png"

# Airtable integration (for Bank Statement Review module)
AIRTABLE_API_KEY = os.environ.get("AIRTABLE_API_KEY", "")
# Airtable table configuration (moved from hardcoded values in views)
AIRTABLE_BASE_ID = os.environ.get("AIRTABLE_BASE_ID", "")
AIRTABLE_PENDING_TABLE = os.environ.get("AIRTABLE_PENDING_TABLE", "")
AIRTABLE_JOBS_TABLE = os.environ.get("AIRTABLE_JOBS_TABLE", "")
AIRTABLE_LEARNING_TABLE = os.environ.get("AIRTABLE_LEARNING_TABLE", "")

# Accounting platform integrations (OAuth2 credentials)
# Xero (Accounting + Practice Manager)
XERO_CLIENT_ID = os.environ.get("XERO_CLIENT_ID", "")
XERO_CLIENT_SECRET = os.environ.get("XERO_CLIENT_SECRET", "")
# XPM sync: set to True to enable Practice Manager sync features
XPM_SYNC_ENABLED = os.environ.get("XPM_SYNC_ENABLED", "false").lower() == "true"

# MYOB
MYOB_CLIENT_ID = os.environ.get("MYOB_CLIENT_ID", "")
MYOB_CLIENT_SECRET = os.environ.get("MYOB_CLIENT_SECRET", "")

# QuickBooks Online
QBO_CLIENT_ID = os.environ.get("QBO_CLIENT_ID", "")
QBO_CLIENT_SECRET = os.environ.get("QBO_CLIENT_SECRET", "")

# AI Provider — Anthropic Claude (for bank statement extraction + transaction classification)
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# django-ratelimit: Use X-Forwarded-For header for IP detection behind reverse proxy (Nginx)
RATELIMIT_IP_META_KEY = "HTTP_X_FORWARDED_FOR"

# Build version timestamp — updated with each deployment
BUILD_TIMESTAMP = "2026-02-23 16:20 AEDT"

# ── Eva AI Practice Intelligence ──────────────────────────────────────────────
# SharePoint / Microsoft Graph API credentials for Knowledge Brain sync
SHAREPOINT_TENANT_ID = os.environ.get("SHAREPOINT_TENANT_ID", "")
SHAREPOINT_CLIENT_ID = os.environ.get("SHAREPOINT_CLIENT_ID", "")
SHAREPOINT_CLIENT_SECRET = os.environ.get("SHAREPOINT_CLIENT_SECRET", "")
SHAREPOINT_SITE_ID = os.environ.get("SHAREPOINT_SITE_ID", "")
SHAREPOINT_DRIVE_ID = os.environ.get("SHAREPOINT_DRIVE_ID", "")

# Eva model configuration
# Default model tier for Eva chat (haiku, sonnet, opus)
EVA_DEFAULT_MODEL = os.environ.get("EVA_DEFAULT_MODEL", "sonnet")
# Enable/disable Eva features globally
EVA_ENABLED = os.environ.get("EVA_ENABLED", "true").lower() == "true"

# ── Celery Task Queue ────────────────────────────────────────────────────────
CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", "redis://localhost:6379/0")
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = "Australia/Melbourne"
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"
CELERY_BEAT_SCHEDULE = {
    "sync-knowledge-brain-nightly": {
        "task": "core.sync_knowledge_brain",
        "schedule": 86400,  # Every 24 hours
        "options": {"expires": 3600},
    },
}

# ── AWS (Textract OCR) ──────────────────────────────────────────────────────
AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
AWS_REGION = os.environ.get("AWS_REGION", "ap-southeast-2")
AWS_TEXTRACT_SNS_TOPIC_ARN = os.environ.get("AWS_TEXTRACT_SNS_TOPIC_ARN", "")
AWS_TEXTRACT_ROLE_ARN = os.environ.get("AWS_TEXTRACT_ROLE_ARN", "")

# ── FuseSign Integration ────────────────────────────────────────────────────
FUSESIGN_API_KEY = os.environ.get("FUSESIGN_API_KEY", "")
FUSESIGN_API_URL = os.environ.get("FUSESIGN_API_URL", "https://api.fusesign.com")
