"""
Django management command: fix_adjustment_mappings

Fixes TrialBalanceLine adjustment rows that were created with the wrong
(or missing) mapped_line_item.  This happens when a journal entry creates
a new adjustment row but does not inherit the mapped_line_item from the
original imported TB line for the same account_code.

What it does:
  1. Finds all adjustment rows (is_adjustment=True) whose mapped_line_item
     differs from the original (non-adjustment) row for the same
     account_code + financial_year.
  2. Updates the adjustment row's mapped_line_item to match the original.
  3. Prints a summary of all changes made.

Usage (on the server):
  cd /opt/statementhub
  source venv/bin/activate
  python manage.py fix_adjustment_mappings          # dry-run (default)
  python manage.py fix_adjustment_mappings --apply  # actually apply fixes
"""

from django.core.management.base import BaseCommand
from core.models import TrialBalanceLine


class Command(BaseCommand):
    help = (
        "Fix adjustment TrialBalanceLine rows whose mapped_line_item "
        "does not match the original imported row for the same account_code."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            default=False,
            help="Actually apply the fixes. Without this flag, runs in dry-run mode.",
        )

    def handle(self, *args, **options):
        apply = options["apply"]
        mode = "APPLY" if apply else "DRY-RUN"
        self.stdout.write(f"\n{'='*60}")
        self.stdout.write(f"  Fix Adjustment Mappings  [{mode}]")
        self.stdout.write(f"{'='*60}\n")

        # Get all adjustment rows
        adjustments = (
            TrialBalanceLine.objects
            .filter(is_adjustment=True)
            .select_related("financial_year", "financial_year__entity", "mapped_line_item")
        )

        fixed = 0
        skipped_no_original = 0
        already_correct = 0

        for adj in adjustments:
            fy = adj.financial_year
            code = adj.account_code

            # Find the original (non-adjustment) row for this account_code
            original = (
                TrialBalanceLine.objects
                .filter(
                    financial_year=fy,
                    account_code=code,
                    is_adjustment=False,
                    mapped_line_item__isnull=False,
                )
                .select_related("mapped_line_item")
                .first()
            )

            if not original:
                # No original row to inherit from - skip
                skipped_no_original += 1
                continue

            if adj.mapped_line_item_id == original.mapped_line_item_id:
                # Already correct
                already_correct += 1
                continue

            # Mismatch found - fix it
            entity_name = fy.entity.name if fy.entity else "Unknown"
            old_mapping = adj.mapped_line_item.description if adj.mapped_line_item else "None"
            new_mapping = original.mapped_line_item.description if original.mapped_line_item else "None"

            self.stdout.write(
                f"  FIX: {entity_name} / FY {fy.end_date.year} / "
                f"Account {code} ({adj.account_name})\n"
                f"       Old mapping: {old_mapping}\n"
                f"       New mapping: {new_mapping}\n"
            )

            if apply:
                adj.mapped_line_item = original.mapped_line_item
                adj.save(update_fields=["mapped_line_item"])

            fixed += 1

        self.stdout.write(f"\n{'='*60}")
        self.stdout.write(f"  Summary:")
        self.stdout.write(f"    Fixed:            {fixed}")
        self.stdout.write(f"    Already correct:  {already_correct}")
        self.stdout.write(f"    No original row:  {skipped_no_original}")
        self.stdout.write(f"{'='*60}")

        if not apply and fixed > 0:
            self.stdout.write(
                f"\n  ** DRY-RUN: No changes were made. "
                f"Run with --apply to fix. **\n"
            )
        elif apply and fixed > 0:
            self.stdout.write(f"\n  ** {fixed} adjustment(s) fixed successfully. **\n")
        else:
            self.stdout.write(f"\n  ** No fixes needed. All mappings are correct. **\n")
