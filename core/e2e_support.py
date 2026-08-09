"""
Shared helpers for the Playwright E2E rig.

Kept outside core/management/commands/ so Django does not mistake it for a
management command.
"""

import os
import tempfile
from pathlib import Path

from django.core.management.base import CommandError
from django.db import connection


def atomic_write(path, writer, *, mode: int = 0o600) -> Path:
    """Publish `path` so a concurrent reader never observes a partial file.

    Every booting E2E instance re-runs e2e_bootstrap_users and
    e2e_seed_fixture_entity, and both write fixed shared paths under .e2e/ while
    other instances may be part-way through reading them. Neither Path.write_text
    nor openpyxl's save is atomic, so a reader can catch a half-written file. The
    content is deterministic, so this is a torn-read hazard rather than a wrong-data
    one — which makes it worse to diagnose, not better: it surfaces as a
    non-reproducible JSON parse error or a corrupt workbook, exactly the shape of
    failure that gets waved off as flake.

    Writing to a temp file in the *same* directory (so the rename cannot cross a
    filesystem boundary and degrade into a copy) and then os.replace-ing it onto the
    target makes the swap atomic: a reader sees either the whole old file or the
    whole new one. `writer` is called with the temp path and produces the content.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        writer(tmp)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return path


def atomic_write_text(path, text: str, *, mode: int = 0o600) -> Path:
    """atomic_write for the common case of writing a string."""
    return atomic_write(path, lambda tmp: tmp.write_text(text), mode=mode)

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
