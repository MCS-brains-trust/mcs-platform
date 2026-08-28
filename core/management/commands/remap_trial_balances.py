"""
Remap all unmapped TrialBalanceLine records using the ChartOfAccount maps_to field.

This fixes existing trial balance lines that were imported before the chart of accounts
had its maps_to field populated.

Usage:
    python manage.py remap_trial_balances
    python manage.py remap_trial_balances --dry-run
"""
from django.core.management.base import BaseCommand
from core.models import (
    TrialBalanceLine, ChartOfAccount, FinancialYear, template_entity_type,
)


class Command(BaseCommand):
    help = "Remap unmapped trial balance lines using ChartOfAccount.maps_to"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview changes without saving",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        # Get all unmapped trial balance lines
        unmapped_lines = TrialBalanceLine.objects.filter(
            mapped_line_item__isnull=True
        ).select_related("financial_year__entity")

        total = unmapped_lines.count()
        self.stdout.write(f"Found {total} unmapped trial balance lines")

        if total == 0:
            self.stdout.write(self.style.SUCCESS("Nothing to do."))
            return

        # Build lookup: (entity_type, account_code) -> AccountMapping
        coa_map = {}
        for coa in ChartOfAccount.objects.filter(
            is_active=True, maps_to__isnull=False
        ).select_related("maps_to"):
            coa_map[(coa.entity_type, coa.account_code)] = coa.maps_to

        self.stdout.write(f"Chart of accounts lookup: {len(coa_map)} mapped entries")

        mapped_count = 0
        still_unmapped = 0

        for line in unmapped_lines:
            entity_type = line.financial_year.entity.entity_type
            # Template rows are keyed by the template type: a unit trust reads
            # the trust chart. The raw type is kept for the log lines below.
            key = (template_entity_type(entity_type), line.account_code)

            if key in coa_map:
                mapping = coa_map[key]
                if not dry_run:
                    line.mapped_line_item = mapping
                    line.save(update_fields=["mapped_line_item"])
                mapped_count += 1
                if dry_run:
                    self.stdout.write(
                        f"  WOULD MAP: {line.account_code} {line.account_name} "
                        f"({entity_type}) -> {mapping.line_item_label}"
                    )
            else:
                still_unmapped += 1
                if dry_run:
                    self.stdout.write(
                        f"  NO MATCH: {line.account_code} {line.account_name} "
                        f"({entity_type})"
                    )

        prefix = "WOULD REMAP" if dry_run else "REMAPPED"
        self.stdout.write(
            self.style.SUCCESS(
                f"{prefix}: {mapped_count}/{total} lines. "
                f"Still unmapped: {still_unmapped}"
            )
        )
