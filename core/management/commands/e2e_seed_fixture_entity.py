"""Seed the deterministic Tier 2 fixture entity (E2E databases only).

    python3 manage.py e2e_seed_fixture_entity --settings=config.settings_e2e

Idempotent, so it runs after every branch. Writes .e2e/fixture_entity.json for the
Playwright specs to read, mirroring how e2e_bootstrap_users publishes its fixtures.
"""
import json
from pathlib import Path

from django.core.management.base import BaseCommand

from core.e2e_fixture_data import seed_fixture_entity
from core.e2e_support import assert_e2e_database
from core.e2e_tb_workbooks import write_tb_workbooks


class Command(BaseCommand):
    help = "Create/reset the Tier 2 fixture entity (E2E databases only)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--output",
            default="/opt/statementhub/.e2e/fixture_entity.json",
            help="Where to write the id manifest consumed by Playwright.",
        )

    def handle(self, *args, **options):
        assert_e2e_database()
        ids = seed_fixture_entity()
        output = Path(options["output"])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(ids, indent=2) + "\n")
        self.stdout.write(self.style.SUCCESS(f"fixture entity seeded → {output}"))
        workbooks = write_tb_workbooks("/opt/statementhub/.e2e/tb")
        self.stdout.write(self.style.SUCCESS(f"tb workbooks written: {len(workbooks)}"))
