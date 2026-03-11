"""
Management command to find and fix trial balance imbalances caused by orphaned
TB lines.

Scans every financial year for cases where total debits != total credits,
identifies orphaned adjustment lines (source_journal IS NULL, not linked to
a bulk upload) that are causing the imbalance, and deletes them.

Usage:
    python manage.py fix_tb_balance                # report only (dry-run)
    python manage.py fix_tb_balance --fix          # report + delete orphans
    python manage.py fix_tb_balance --entity "Foo" # filter by entity name
"""
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Sum

from core.models import FinancialYear, TrialBalanceLine


class Command(BaseCommand):
    help = (
        "Find financial years where total DR != total CR and delete orphaned "
        "adjustment TB lines that cause the imbalance"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--fix",
            action="store_true",
            help="Delete orphaned TB lines to restore balance (default is dry-run)",
        )
        parser.add_argument(
            "--entity",
            type=str,
            help="Limit to a specific entity by name (substring match)",
        )

    def handle(self, *args, **options):
        fix = options["fix"]
        entity_filter = options.get("entity")

        fy_qs = FinancialYear.objects.select_related("entity").all()
        if entity_filter:
            fy_qs = fy_qs.filter(entity__entity_name__icontains=entity_filter)

        total_checked = 0
        total_balanced = 0
        total_imbalanced = 0
        total_fixed = 0
        total_unfixable = 0

        for fy in fy_qs.iterator(chunk_size=200):
            total_checked += 1
            entity_name = fy.entity.entity_name

            totals = TrialBalanceLine.objects.filter(
                financial_year=fy,
            ).aggregate(total_dr=Sum("debit"), total_cr=Sum("credit"))

            total_dr = totals["total_dr"] or Decimal("0")
            total_cr = totals["total_cr"] or Decimal("0")

            if total_dr == total_cr:
                total_balanced += 1
                continue

            diff = total_dr - total_cr
            total_imbalanced += 1

            self.stdout.write(
                self.style.WARNING(
                    f"  IMBALANCED: {entity_name} / {fy.year_label} — "
                    f"DR={total_dr}, CR={total_cr}, diff={diff}"
                )
            )

            # Find orphaned adjustment lines: manual_journal source with no
            # linked journal and no linked bulk upload.  These are the most
            # likely cause of imbalance — leftover lines from incomplete
            # journal deletions or failed posting operations.
            orphans = TrialBalanceLine.objects.filter(
                financial_year=fy,
                is_adjustment=True,
                source="manual_journal",
                source_journal__isnull=True,
                bulk_journal_upload__isnull=True,
            )

            orphan_totals = orphans.aggregate(
                orphan_dr=Sum("debit"), orphan_cr=Sum("credit"),
            )
            orphan_dr = orphan_totals["orphan_dr"] or Decimal("0")
            orphan_cr = orphan_totals["orphan_cr"] or Decimal("0")
            orphan_count = orphans.count()

            if orphan_count == 0:
                self.stdout.write(
                    self.style.ERROR(
                        f"    No orphaned manual_journal lines found — "
                        f"imbalance has a different cause"
                    )
                )
                total_unfixable += 1
                continue

            orphan_net = orphan_dr - orphan_cr

            self.stdout.write(
                f"    Found {orphan_count} orphaned line(s): "
                f"DR={orphan_dr}, CR={orphan_cr}, net={orphan_net}"
            )

            # Check if deleting ALL orphans would restore balance
            remaining_dr = total_dr - orphan_dr
            remaining_cr = total_cr - orphan_cr

            if remaining_dr == remaining_cr:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"    Deleting all {orphan_count} orphan(s) would "
                        f"restore balance (DR=CR={remaining_dr})"
                    )
                )
                if fix:
                    with transaction.atomic():
                        deleted, _ = orphans.delete()
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"    → Deleted {deleted} orphaned TB line(s)"
                        )
                    )
                    total_fixed += 1
                continue

            # Deleting all orphans wouldn't perfectly restore balance.
            # Try to find the minimal subset that matches the imbalance.
            # Strategy: find orphans whose net DR-CR equals the imbalance.
            self.stdout.write(
                self.style.WARNING(
                    f"    Deleting all orphans would leave "
                    f"DR={remaining_dr}, CR={remaining_cr} — "
                    f"attempting targeted deletion"
                )
            )

            # Try matching individual orphan lines whose amounts equal
            # the imbalance exactly.
            fixed_targeted = False
            if diff > 0:
                # More debits than credits — look for orphan debit lines
                targets = orphans.filter(
                    debit=diff, credit=Decimal("0"),
                )
            else:
                # More credits than debits — look for orphan credit lines
                targets = orphans.filter(
                    credit=-diff, debit=Decimal("0"),
                )

            if targets.exists():
                target = targets.first()
                self.stdout.write(
                    self.style.SUCCESS(
                        f"    Found exact-match orphan: {target.account_code} "
                        f"DR={target.debit} CR={target.credit}"
                    )
                )
                if fix:
                    with transaction.atomic():
                        target.delete()
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"    → Deleted 1 targeted orphaned TB line"
                        )
                    )
                    total_fixed += 1
                fixed_targeted = True

            if not fixed_targeted:
                # Fall back: if deleting all orphans gets us closer to
                # balance, do that; otherwise report as unfixable.
                if abs(remaining_dr - remaining_cr) < abs(diff):
                    self.stdout.write(
                        self.style.WARNING(
                            f"    Deleting all orphans reduces imbalance "
                            f"from {diff} to {remaining_dr - remaining_cr}"
                        )
                    )
                    if fix:
                        with transaction.atomic():
                            deleted, _ = orphans.delete()
                        self.stdout.write(
                            self.style.SUCCESS(
                                f"    → Deleted {deleted} orphaned TB line(s) "
                                f"(partial fix)"
                            )
                        )
                        total_fixed += 1
                else:
                    self.stdout.write(
                        self.style.ERROR(
                            f"    Cannot auto-fix — orphans do not explain "
                            f"the imbalance. Manual investigation required."
                        )
                    )
                    total_unfixable += 1

        # ── Summary ──────────────────────────────────────────────────────
        self.stdout.write("")
        self.stdout.write("=" * 60)
        self.stdout.write(f"  Financial years checked:  {total_checked}")
        self.stdout.write(
            self.style.SUCCESS(f"  Balanced:                 {total_balanced}")
        )
        if total_imbalanced:
            self.stdout.write(
                self.style.WARNING(
                    f"  Imbalanced:               {total_imbalanced}"
                )
            )
        if total_fixed:
            self.stdout.write(
                self.style.SUCCESS(f"  Fixed:                    {total_fixed}")
            )
        if total_unfixable:
            self.stdout.write(
                self.style.ERROR(
                    f"  Unfixable (manual check): {total_unfixable}"
                )
            )
        if total_imbalanced and not fix:
            self.stdout.write(
                self.style.WARNING(
                    f"\n  {total_imbalanced} imbalanced FY(s) found. "
                    f"Use --fix to delete orphaned lines."
                )
            )
        self.stdout.write("=" * 60)

        if total_imbalanced == 0:
            self.stdout.write(
                self.style.SUCCESS(
                    "\nAll financial years are balanced (DR == CR)."
                )
            )
