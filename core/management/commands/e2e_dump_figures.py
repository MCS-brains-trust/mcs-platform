"""Dump one financial year's figures as canonical JSON (E2E databases only).

    python3 manage.py e2e_dump_figures --year <uuid> --checkpoint after_tb_commit \
        --settings=config.settings_e2e

Read-only, but marker-guarded like every other e2e_* command so it cannot be pointed
at production by omitting --settings.
"""
import json
import sys

from django.core.management.base import BaseCommand, CommandError

from core.e2e_figures import dump_figures
from core.e2e_support import assert_e2e_database


class Command(BaseCommand):
    help = "Dump a financial year's figures as canonical JSON (E2E databases only)."

    def add_arguments(self, parser):
        parser.add_argument("--year", required=True, help="FinancialYear UUID")
        parser.add_argument("--checkpoint", required=True, help="Label for this snapshot")
        parser.add_argument("--output", default="-", help="File path, or - for stdout")

    def handle(self, *args, **options):
        assert_e2e_database()
        from core.models import FinancialYear

        try:
            year = FinancialYear.objects.get(pk=options["year"])
        except FinancialYear.DoesNotExist:
            raise CommandError(f"no financial year with pk {options['year']}")

        payload = {"checkpoint": options["checkpoint"], "figures": dump_figures(year)}
        text = json.dumps(payload, indent=2, sort_keys=True) + "\n"

        if options["output"] == "-":
            sys.stdout.write(text)
        else:
            with open(options["output"], "w") as handle:
                handle.write(text)
