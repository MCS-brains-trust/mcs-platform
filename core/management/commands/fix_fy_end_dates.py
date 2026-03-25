"""Fix FinancialYear records where end_date falls on the 1st of a month.

This indicates the off-by-one bug where end_date was set to the first day
of the next period instead of the last day of the current period.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import F

from core.models import FinancialYear


class Command(BaseCommand):
    help = "Fix FY end_dates that fall on the 1st of a month (off-by-one bug)"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Show what would change without saving")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        bad_fys = FinancialYear.objects.filter(end_date__day=1)
        count = bad_fys.count()

        if count == 0:
            self.stdout.write(self.style.SUCCESS("No FY records with end_date on the 1st — nothing to fix."))
            return

        self.stdout.write(f"Found {count} FY records with end_date on the 1st of a month:")
        for fy in bad_fys.select_related("entity"):
            old = fy.end_date
            new = old - timedelta(days=1)
            self.stdout.write(f"  {fy.entity.entity_name} {fy.year_label}: {old} → {new}")

        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run — no changes made."))
            return

        updated = FinancialYear.objects.filter(end_date__day=1).update(
            end_date=F("end_date") - timedelta(days=1)
        )
        self.stdout.write(self.style.SUCCESS(f"Fixed {updated} FY end_date records."))
