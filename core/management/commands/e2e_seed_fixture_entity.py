"""Seed the deterministic Tier 2 fixture entities (E2E databases only).

    python3 manage.py e2e_seed_fixture_entity --settings=config.settings_e2e
    python3 manage.py e2e_seed_fixture_entity --profile trust --settings=config.settings_e2e

Idempotent, so it runs after every branch. Writes one id manifest per profile for
the Playwright specs to read, mirroring how e2e_bootstrap_users publishes its
fixtures.

The company keeps the original unsuffixed filename because tier2/roll_forward.spec.ts
and tier2/yearend_close.spec.ts already read that exact path.
"""
import json

from django.core.management.base import BaseCommand

from core.e2e_fixture_data import PROFILES, seed_fixture_entity
from core.e2e_support import assert_e2e_database, atomic_write_text
from core.e2e_tb_workbooks import write_tb_workbooks


def manifest_name(key: str) -> str:
    return "fixture_entity.json" if key == "company" else f"fixture_entity_{key}.json"


class Command(BaseCommand):
    help = "Create/reset the Tier 2 fixture entities (E2E databases only)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--output-dir",
            default="/opt/statementhub/.e2e",
            help="Where to write the id manifests consumed by Playwright.",
        )
        parser.add_argument(
            "--profile",
            action="append",
            choices=sorted(PROFILES),
            help="Seed only this profile (repeatable). Default: all of them.",
        )

    def handle(self, *args, **options):
        assert_e2e_database()
        keys = options["profile"] or sorted(PROFILES)
        for key in keys:
            ids = seed_fixture_entity(PROFILES[key])
            output = atomic_write_text(
                f"{options['output_dir']}/{manifest_name(key)}",
                json.dumps(ids, indent=2) + "\n",
            )
            self.stdout.write(self.style.SUCCESS(f"{key} seeded → {output}"))
        workbooks = write_tb_workbooks("/opt/statementhub/.e2e/tb")
        self.stdout.write(self.style.SUCCESS(f"tb workbooks written: {len(workbooks)}"))
