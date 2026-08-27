"""
Restore the entity chart's account name onto trial balance rows that an
import renamed.

Found on Minli Enterprise Unit Trust FY2026: a Xero import wrote the Xero
account name against the StatementHub chart code it had been mapped to, so
620 read "Rental Income" where the chart says "Rents received" and the bank
account read "PENMAN PROPERTY NOMINEES PTY L" instead of "Cash at bank".
The rows are correct in every respect but the name.

integrations/views.py now takes the name from the chart at commit time, so
no new row can acquire a source name. This command repairs the rows written
before that fix.

Rows whose account_code has no entry in the entity chart are left untouched:
there is no authoritative name to apply, and re-homing orphaned codes is
separate work.

Usage:
    python3 manage.py backfill_tb_account_names --dry-run
    python3 manage.py backfill_tb_account_names --entity "Minli"
    python3 manage.py backfill_tb_account_names --include-finalised
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import Entity, EntityChartOfAccount, TrialBalanceLine


class Command(BaseCommand):
    help = "Rewrite trial balance account names from the entity chart of accounts"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change without writing anything.",
        )
        parser.add_argument(
            "--entity",
            default="",
            help="Only process entities whose name contains this text.",
        )
        parser.add_argument(
            "--include-finalised",
            action="store_true",
            help=(
                "Also correct finalised years. Off by default because renaming "
                "an account in a finalised year changes financial statements "
                "that have already been issued."
            ),
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        entity_filter = options["entity"]
        include_finalised = options["include_finalised"]

        entities = Entity.objects.all().order_by("entity_name")
        if entity_filter:
            entities = entities.filter(entity_name__icontains=entity_filter)

        total_fixed = 0
        total_skipped_finalised = 0
        entities_touched = 0

        for entity in entities:
            chart = dict(
                EntityChartOfAccount.objects
                .filter(entity=entity)
                .values_list("account_code", "account_name")
            )
            if not chart:
                continue

            lines = (
                TrialBalanceLine.objects
                .filter(financial_year__entity=entity)
                .select_related("financial_year")
                .order_by("financial_year__end_date", "account_code")
            )

            pending = []
            for line in lines:
                chart_name = chart.get(line.account_code)
                if not chart_name:
                    continue
                if chart_name.strip() == (line.account_name or "").strip():
                    continue
                if (
                    line.financial_year.status == "finalised"
                    and not include_finalised
                ):
                    total_skipped_finalised += 1
                    continue
                pending.append((line, chart_name))

            if not pending:
                continue

            entities_touched += 1
            self.stdout.write(self.style.MIGRATE_HEADING(entity.entity_name))
            for line, chart_name in pending:
                self.stdout.write(
                    f"  {line.financial_year.year_label} "
                    f"{line.account_code:<10} "
                    f"{line.account_name!r} -> {chart_name!r} "
                    f"[{line.source}]"
                )

            if not dry_run:
                with transaction.atomic():
                    for line, chart_name in pending:
                        line.account_name = chart_name
                        line.save(update_fields=["account_name"])

            total_fixed += len(pending)

        verb = "would rename" if dry_run else "renamed"
        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                f"{verb} {total_fixed} trial balance row(s) "
                f"across {entities_touched} entit(ies)."
            )
        )
        if total_skipped_finalised:
            self.stdout.write(
                self.style.WARNING(
                    f"skipped {total_skipped_finalised} row(s) in finalised years "
                    f"— re-run with --include-finalised to correct those too."
                )
            )
