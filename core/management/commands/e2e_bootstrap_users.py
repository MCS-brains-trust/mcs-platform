"""
Create the E2E role fixture users in a hardened E2E database.

    python3 manage.py e2e_bootstrap_users --settings=config.settings_e2e

One user per role, each with a fixed TOTP secret so the Playwright global setup can
derive a valid code without a shared secret store. Writes .e2e/users.json for the
suite to read.

Idempotent: re-running resets the fixtures to a known state rather than failing, so
it can run after every database refresh.
"""

import json
import os
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from core.e2e_support import E2E_PASSWORD, E2E_ROLES, assert_e2e_database

ASSIGNMENT_COUNT = 3


class Command(BaseCommand):
    help = "Create/reset the E2E role fixture users (E2E databases only)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--output",
            default="/opt/statementhub/.e2e/users.json",
            help="Where to write the fixture manifest consumed by Playwright.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        assert_e2e_database()

        from core.models import Entity

        User = get_user_model()
        manifest = {"password": E2E_PASSWORD, "roles": {}}

        # Assign real entities to the roles that cannot view all entities, so their
        # crawl exercises genuine permission behaviour rather than an empty account.
        assignable = list(Entity.objects.order_by("entity_name")[:ASSIGNMENT_COUNT])
        if not assignable:
            self.stderr.write(self.style.WARNING(
                "no entities in this database — restricted roles will have nothing to view"
            ))

        for key, spec in E2E_ROLES.items():
            user, created = User.objects.update_or_create(
                username=spec["username"],
                defaults={
                    "role": spec["role"],
                    "email": f"{spec['username']}@e2e.statementhub.invalid",
                    "first_name": "E2E",
                    "last_name": spec["role"].replace("_", " ").title(),
                    "is_active": True,
                    "is_staff": spec["is_staff"],
                    "is_superuser": spec["is_superuser"],
                    # Require2FAMiddleware diverts any user without confirmed 2FA to
                    # the mandatory setup page, which would strand every test on a
                    # setup screen. Both fields are required for has_2fa
                    # (accounts/models.py:90).
                    "totp_secret": spec["totp_secret"],
                    "totp_confirmed": True,
                },
            )
            user.set_password(E2E_PASSWORD)
            user.save(update_fields=["password"])

            assigned = []
            if spec["needs_assignments"] and assignable:
                for entity in assignable:
                    entity.assigned_accountant = user
                    entity.save(update_fields=["assigned_accountant"])
                    assigned.append(str(entity.pk))

            manifest["roles"][key] = {
                "username": spec["username"],
                "role": spec["role"],
                "totp_secret": spec["totp_secret"],
                "is_staff": spec["is_staff"],
                "assigned_entities": assigned,
            }

            self.stdout.write(
                f"{'created' if created else 'reset'} {spec['username']} "
                f"({spec['role']}{', ' + str(len(assigned)) + ' entities' if assigned else ''})"
            )

        # Recorded so the crawl can use real primary keys for URL parameters instead
        # of inventing UUIDs that would only ever produce 404s.
        manifest["sample_entities"] = [
            {"id": str(e.pk), "name": e.entity_name, "type": e.entity_type}
            for e in Entity.objects.order_by("entity_name")[:10]
        ]

        out_path = Path(options["output"])
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(manifest, indent=2))
        os.chmod(out_path, 0o600)

        self.stdout.write(self.style.SUCCESS(
            f"wrote {len(manifest['roles'])} role fixtures to {out_path}"
        ))
