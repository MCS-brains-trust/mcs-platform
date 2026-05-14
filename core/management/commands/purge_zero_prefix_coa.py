"""
purge_zero_prefix_coa.py

Management command to delete all ChartOfAccount master records whose
account_code starts with '0' for company, partnership, and sole_trader
entity types.

Trust accounts are intentionally excluded — their 0xxx codes are unique
and have no non-zero counterparts.

Usage:
    python3 manage.py purge_zero_prefix_coa            # dry run
    python3 manage.py purge_zero_prefix_coa --apply    # apply deletion
"""

from django.core.management.base import BaseCommand

from core.models import ChartOfAccount

ENTITY_TYPES_TO_CLEAN = ["company", "partnership", "sole_trader"]


class Command(BaseCommand):
    help = (
        "Delete ChartOfAccount records whose code starts with '0' for "
        "company, partnership, and sole_trader entity types."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Actually delete the records (default is dry-run).",
        )

    def handle(self, *args, **options):
        apply = options["apply"]
        mode = "APPLYING" if apply else "DRY RUN"

        self.stdout.write(f"\n{'=' * 60}")
        self.stdout.write(f"  Purge Zero-Prefix ChartOfAccount Records — {mode}")
        self.stdout.write(f"{'=' * 60}\n")

        total_deleted = 0

        for entity_type in ENTITY_TYPES_TO_CLEAN:
            qs = ChartOfAccount.objects.filter(
                entity_type=entity_type,
                account_code__startswith="0",
            )
            count = qs.count()

            self.stdout.write(
                f"  [{entity_type.upper()}] {count} record(s) to remove:"
            )
            for acct in qs.order_by("account_code"):
                self.stdout.write(
                    f"    {acct.account_code}  {acct.account_name}"
                )

            if apply and count:
                qs.delete()
                self.stdout.write(
                    self.style.SUCCESS(f"    → Deleted {count} record(s).")
                )

            total_deleted += count

        self.stdout.write(f"\n{'=' * 60}")
        self.stdout.write(f"  Summary ({mode}): {total_deleted} total record(s) affected.")
        self.stdout.write(f"{'=' * 60}\n")

        if not apply:
            self.stdout.write(
                self.style.NOTICE(
                    "  This was a DRY RUN. Run with --apply to delete records."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS("  Done. Zero-prefix accounts removed from master CoA.")
            )
