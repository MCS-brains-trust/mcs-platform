"""
Settings for the Playwright E2E instance.

Inherits production settings wholesale and overrides only what must differ, so
the suite exercises the real configuration rather than a simplified test one.
DEBUG stays False for that reason: the suite should see production's error pages,
CSP policy, and template behaviour, not the debug variants.

Run with:
    DJANGO_SETTINGS_MODULE=config.settings_e2e

The E2E_DB_NAME environment variable selects which database to use, so a Playwright
worker can point this at its own branched copy of the template.

Safety: this module refuses to import unless the target database is loopback and
carries the e2e_marker table written by scripts/e2e_harden.sql. A mistyped
DATABASE_URL therefore fails closed at startup instead of running the suite —
which creates, finalises and deletes records — against production.
"""

import os
import sys
from pathlib import Path

from config.settings import *  # noqa: F401,F403

# Runtime state, not code: the venv and .e2e belong to the machine and stay at the
# deployment root even when the checkout under test lives elsewhere (CI). See
# e2e/fixtures/paths.ts.
E2E_DIR = Path(os.environ.get("STATEMENTHUB_RUNTIME_ROOT", "/opt/statementhub")) / ".e2e"


def _load_db_env():
    """Read .e2e/db.env, which holds the local cluster credentials."""
    env_file = E2E_DIR / "db.env"
    values = {}
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip()
    return values


_db = _load_db_env()

E2E_DB_HOST = os.environ.get("E2E_DB_HOST", _db.get("E2E_DB_HOST", "127.0.0.1"))
E2E_DB_PORT = os.environ.get("E2E_DB_PORT", _db.get("E2E_DB_PORT", "5433"))
E2E_DB_USER = os.environ.get("E2E_DB_USER", _db.get("E2E_DB_USER", "e2e"))
E2E_DB_PASSWORD = os.environ.get("E2E_DB_PASSWORD", _db.get("E2E_DB_PASSWORD", ""))
E2E_DB_NAME = os.environ.get("E2E_DB_NAME", _db.get("E2E_TEMPLATE_DB", "sh_e2e_template"))


# --- Fail closed ------------------------------------------------------------

def _assert_safe_target():
    if E2E_DB_HOST not in ("127.0.0.1", "::1", "localhost"):
        raise RuntimeError(
            f"settings_e2e refuses to run against non-loopback host {E2E_DB_HOST!r}. "
            "The E2E suite writes, finalises and deletes records."
        )

    # Skip the marker probe for commands that legitimately run before a database
    # exists, so provisioning is not a chicken-and-egg problem.
    if any(arg in sys.argv for arg in ("collectstatic", "makemigrations", "help", "--help")):
        return

    import psycopg2

    try:
        conn = psycopg2.connect(
            host=E2E_DB_HOST, port=E2E_DB_PORT, user=E2E_DB_USER,
            password=E2E_DB_PASSWORD, dbname=E2E_DB_NAME, connect_timeout=5,
        )
    except psycopg2.OperationalError as exc:
        raise RuntimeError(
            f"settings_e2e cannot reach {E2E_DB_NAME} on {E2E_DB_HOST}:{E2E_DB_PORT}. "
            f"Run scripts/refresh_e2e_db.sh first. Underlying error: {exc}"
        ) from exc

    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.e2e_marker');")
            if cur.fetchone()[0] is None:
                raise RuntimeError(
                    f"database {E2E_DB_NAME!r} has no e2e_marker table. This is the guard "
                    "against pointing the E2E instance at production — refusing to start."
                )
            cur.execute("SELECT bool_or(is_e2e) FROM e2e_marker;")
            if not cur.fetchone()[0]:
                raise RuntimeError(f"e2e_marker in {E2E_DB_NAME!r} is not set — refusing to start.")
    finally:
        conn.close()


_assert_safe_target()


# --- Database ---------------------------------------------------------------

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": E2E_DB_NAME,
        "USER": E2E_DB_USER,
        "PASSWORD": E2E_DB_PASSWORD,
        "HOST": E2E_DB_HOST,
        "PORT": E2E_DB_PORT,
        "CONN_MAX_AGE": 0,
        "CONN_HEALTH_CHECKS": False,
    }
}


# --- Transport security -----------------------------------------------------
# Production enables these behind TLS. The E2E instance is plain HTTP on
# loopback, so leaving them on would 301 every request to a port serving no TLS,
# and would stop the session and CSRF cookies being set at all.

SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
SECURE_HSTS_SECONDS = 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_HSTS_PRELOAD = False

E2E_PORT = os.environ.get("E2E_PORT", "8100")
E2E_BASE_URL = f"http://127.0.0.1:{E2E_PORT}"

ALLOWED_HOSTS = ["127.0.0.1", "localhost", "testserver"]
CSRF_TRUSTED_ORIGINS = [E2E_BASE_URL, f"http://localhost:{E2E_PORT}"]


# --- Rate limiting ----------------------------------------------------------
# Login and TOTP verification are capped at 5/min per IP (accounts/views.py:60,92).
# Every request in the suite originates from 127.0.0.1, so authenticating several
# role fixtures in one global-setup burst would trip the limiter and fail the run
# for reasons unrelated to the code under test.
#
# The limiter is therefore disabled here and covered separately by a Django unit
# test rather than end-to-end, so the behaviour is still asserted somewhere.

RATELIMIT_ENABLE = False


# --- Media ------------------------------------------------------------------
# Production hardcodes MEDIA_ROOT to BASE_DIR/"media". Without this override the
# suite's generated statements and packages would be written into production's
# media tree.

MEDIA_ROOT = E2E_DIR / "media"
os.makedirs(MEDIA_ROOT, exist_ok=True)


# --- Redis allocation -------------------------------------------------------
# Production occupies DB 0 (Celery broker) and DB 1 (cache, and therefore the
# cached_db session store too). The E2E instance must not touch either: sharing
# DB 0 would let the *production* worker consume E2E tasks and execute them
# against the production database, defeating the isolated copy entirely, and
# sharing DB 1 would let the suite evict live user sessions.
#
# DB 2 is left as a deliberate gap so a future production cache split does not
# silently land on top of the E2E rig.
#
#   DB 0  production Celery broker
#   DB 1  production cache + sessions
#   DB 2  (reserved, unused)
#   DB 3  E2E Celery broker + results
#   DB 4  E2E cache + sessions

CELERY_BROKER_URL = os.environ.get("E2E_CELERY_BROKER_URL", "redis://127.0.0.1:6379/3")
CELERY_RESULT_BACKEND = os.environ.get("E2E_CELERY_RESULT_BACKEND", "redis://127.0.0.1:6379/3")

# The queue name is separated as well as the DB index. The DB index alone is the
# real boundary, but a distinct queue means that even if both instances were ever
# pointed at the same index by mistake, the production worker (started without
# -Q e2e) would not consume E2E tasks.
CELERY_TASK_DEFAULT_QUEUE = "e2e"
CELERY_TASK_DEFAULT_ROUTING_KEY = "e2e"


# --- Cache ------------------------------------------------------------------
# Backend and TIMEOUT deliberately mirror production so cache-dependent behaviour
# is exercised the same way; only the DB index differs. Sessions are cached_db in
# production and inherit this cache, so this override also isolates E2E sessions.

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": os.environ.get("E2E_CACHE_URL", "redis://127.0.0.1:6379/4"),
        "TIMEOUT": 300,
        # Instances share redis DB 4, and SESSION_ENGINE is cached_db. Without a
        # per-instance prefix, a session created against one instance's database is
        # visible to another instance that branched before that session existed —
        # which surfaces as an intermittent, baffling auth failure. start_server.sh
        # sets this to the branch database name.
        "KEY_PREFIX": os.environ.get("E2E_CACHE_PREFIX", ""),
    }
}


# --- Email ------------------------------------------------------------------
# Production sends through the Resend HTTP API. The suite points at a sandbox SMTP
# inbox instead, so invitation and password-reset flows genuinely send and can be
# asserted on, without any message reaching a real recipient. Note this exercises
# the SMTP path rather than production's Resend backend.
#
# Until E2E_EMAIL_HOST is configured, fall back to locmem: captured in memory,
# never transmitted. That fails safe — an unconfigured rig cannot email a client.

_e2e_email_host = os.environ.get("E2E_EMAIL_HOST", "")

if _e2e_email_host:
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_HOST = _e2e_email_host
    EMAIL_PORT = int(os.environ.get("E2E_EMAIL_PORT", "2525"))
    EMAIL_HOST_USER = os.environ.get("E2E_EMAIL_HOST_USER", "")
    EMAIL_HOST_PASSWORD = os.environ.get("E2E_EMAIL_HOST_PASSWORD", "")
    EMAIL_USE_TLS = os.environ.get("E2E_EMAIL_USE_TLS", "True").lower() == "true"
    EMAIL_USE_SSL = False
else:
    EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

RESEND_API_KEY = ""
DEFAULT_FROM_EMAIL = "StatementHub E2E <e2e@statementhub.com.au>"


# --- Textract ---------------------------------------------------------------
# Deliberately left unset. core/ocr_service.py:255 only attaches a
# NotificationChannel when both of these are present, so clearing them keeps
# Textract live while avoiding the need to expose a second public SNS webhook and
# IAM role for a loopback-only instance. The suite closes the loop itself by
# polling for job completion and dispatching process_textract_result.

AWS_TEXTRACT_SNS_TOPIC_ARN = ""
AWS_TEXTRACT_ROLE_ARN = ""


# --- Logging ----------------------------------------------------------------
# DEBUG is False, so the browser never sees a traceback. Tracebacks go to a log file
# on the droplet instead, keeping production-like responses without sacrificing
# diagnosability.
#
# That log is written at DEBUG level against a restored copy of the production
# database, so it carries real client financial data in query text and request
# payloads. It therefore stays on the box: the CI workflow deliberately does not
# upload it (see .github/workflows/e2e.yml), and it is kept 0600 in a 0700 directory
# rather than the 0644/0755 the default umask would give it. The log file is
# pre-created with the right mode because logging.FileHandler opens in append mode —
# it will inherit an existing file's permissions, but creates a world-readable one
# if it has to make the file itself.

E2E_LOG_DIR = E2E_DIR / "logs"
os.makedirs(E2E_LOG_DIR, exist_ok=True)
os.chmod(E2E_LOG_DIR, 0o700)
E2E_LOG_FILE = E2E_LOG_DIR / "django.log"
E2E_LOG_FILE.touch(mode=0o600, exist_ok=True)
os.chmod(E2E_LOG_FILE, 0o600)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {"format": "{asctime} {levelname} {name} {message}", "style": "{"},
    },
    "handlers": {
        "file": {
            "level": "DEBUG",
            "class": "logging.FileHandler",
            "filename": str(E2E_LOG_FILE),
            "formatter": "verbose",
        },
        "console": {
            "level": "INFO",
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "loggers": {
        "django.request": {"handlers": ["file", "console"], "level": "DEBUG", "propagate": False},
        "django": {"handlers": ["file"], "level": "INFO", "propagate": False},
        "core": {"handlers": ["file", "console"], "level": "DEBUG", "propagate": False},
        "review": {"handlers": ["file", "console"], "level": "DEBUG", "propagate": False},
        "integrations": {"handlers": ["file", "console"], "level": "DEBUG", "propagate": False},
    },
    "root": {"handlers": ["file"], "level": "INFO"},
}


# The Tier 3 AI pass sends trial balance figures to the Anthropic API. This suite
# finalises a financial year against a production-derived copy of the client
# database, so leaving it on would send real client financials off the droplet on
# every nightly run -- the same contradiction the workflow header rules out for the
# database dump and the DEBUG log -- and bill for the privilege.
AUTO_TIER3_ON_IN_REVIEW = False
