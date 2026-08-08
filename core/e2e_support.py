"""
Shared helpers for the Playwright E2E rig.

Kept outside core/management/commands/ so Django does not mistake it for a
management command.
"""

from django.core.management.base import CommandError
from django.db import connection

# Fixed credentials for the E2E role fixtures. Deterministic by design: the
# Playwright global setup needs to derive a valid TOTP code without a shared
# secret store, and a rotating secret would make a failed run unreproducible.
#
# These are safe to commit only because they are meaningless outside a loopback
# database that has been hardened by scripts/e2e_harden.sql. They must never be
# used to create a user anywhere else, which is what assert_e2e_database() below
# enforces.
E2E_PASSWORD = "e2e-Playwright-Suite-2026"  # noqa: S105 — test fixture, loopback only

# Valid base32, one per role, so a failure identifies its role from the code alone.
E2E_ROLES = {
    "admin": {
        "username": "e2e_admin",
        "role": "admin",
        "totp_secret": "E2EADMINAAAAAAAAAAAAAAAAAAAAAAAA",
        "is_staff": True,
        "is_superuser": True,
        "needs_assignments": False,
    },
    "senior": {
        "username": "e2e_senior",
        "role": "senior_accountant",
        "totp_secret": "E2ESENIORAAAAAAAAAAAAAAAAAAAAAAA",
        "is_staff": False,
        "is_superuser": False,
        "needs_assignments": False,
    },
    "accountant": {
        "username": "e2e_accountant",
        "role": "accountant",
        "totp_secret": "E2EACCOUNTANTAAAAAAAAAAAAAAAAAAA",
        "is_staff": False,
        "is_superuser": False,
        # can_view_all_entities excludes this role (accounts/models.py:62), so
        # without assigned entities most entity pages are inaccessible and the
        # crawl would report false negatives instead of real permission coverage.
        "needs_assignments": True,
    },
    "office_admin": {
        "username": "e2e_office_admin",
        "role": "office_admin",
        "totp_secret": "E2EOFFICEADMINAAAAAAAAAAAAAAAAAA",
        "is_staff": False,
        "is_superuser": False,
        "needs_assignments": False,
    },
    "read_only": {
        "username": "e2e_read_only",
        "role": "read_only",
        "totp_secret": "E2EREADONLYAAAAAAAAAAAAAAAAAAAAA",
        "is_staff": False,
        "is_superuser": False,
        "needs_assignments": True,
    },
}


def assert_e2e_database():
    """Abort unless the connected database is a hardened E2E copy.

    The E2E commands create users, assign entities and mutate financial data. Run
    against production via an omitted --settings flag they would do real damage.
    config/settings_e2e.py already refuses to import against anything else, but
    these commands must not rely on being invoked through it, because the failure
    being guarded is precisely someone forgetting to.

    Raises CommandError so Django reports a clean failure rather than a traceback.
    """
    with connection.cursor() as cur:
        cur.execute("SELECT to_regclass('public.e2e_marker');")
        if cur.fetchone()[0] is None:
            raise CommandError(
                f"refusing to run: database '{connection.settings_dict.get('NAME')}' on "
                f"'{connection.settings_dict.get('HOST')}' has no e2e_marker table, so it is not "
                "a hardened E2E copy. This command mutates data. "
                "Did you mean --settings=config.settings_e2e?"
            )

        cur.execute("SELECT bool_or(is_e2e) FROM e2e_marker;")
        if not cur.fetchone()[0]:
            raise CommandError("refusing to run: e2e_marker is present but is_e2e is not set.")
