"""
Management command to repair a specific journal's TB posting.

Deletes all existing TB lines for the journal (FK-linked and unlinked
orphans) and re-posts using the aggregated posting logic (one TB line
per unique account code, with summed debits and credits).

Usage:
    python manage.py repair_journal_posting <journal_uuid>
    python manage.py repair_journal_posting 008d50b5-22e3-465d-9abd-8e0a63774822
"""
from collections import OrderedDict
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import (
    AdjustingJournal,
    JournalLine,
    TrialBalanceLine,
)


class Command(BaseCommand):
    help = "Repair a journal's TB posting by reversing and re-posting with aggregation"

    def add_arguments(self, parser):
        parser.add_argument(
            "journal_id",
            type=str,
            help="UUID of the journal to repair",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be done without making changes",
        )

    def handle(self, *args, **options):
        journal_id = options["journal_id"]
        dry_run = options["dry_run"]

        try:
            journal = AdjustingJournal.objects.select_related(
                "financial_year", "financial_year__entity",
            ).prefetch_related("lines").get(pk=journal_id)
        except AdjustingJournal.DoesNotExist:
            raise CommandError(f"Journal {journal_id} not found.")

        fy = journal.financial_year
        entity = fy.entity

        self.stdout.write(f"\nJournal: {journal.reference_number} ({journal.pk})")
        self.stdout.write(f"Entity:  {entity.entity_name}")
        self.stdout.write(f"FY:      {fy.year_label}")
        self.stdout.write(f"Status:  {journal.status}")
        self.stdout.write(f"Desc:    {journal.description}")

        jnl_lines = list(journal.lines.order_by("line_number", "id"))
        if not jnl_lines:
            raise CommandError("Journal has no lines.")

        # ── Show journal lines ──
        self.stdout.write(f"\n{'─' * 70}")
        self.stdout.write("JOURNAL LINES:")
        total_jnl_dr = Decimal("0")
        total_jnl_cr = Decimal("0")
        for jl in jnl_lines:
            self.stdout.write(
                f"  L{jl.line_number:02d}  {jl.account_code:<10} "
                f"{jl.account_name:<30} Dr={jl.debit:>12,.2f}  Cr={jl.credit:>12,.2f}"
            )
            total_jnl_dr += jl.debit
            total_jnl_cr += jl.credit
        self.stdout.write(f"  {'':>42} Dr={total_jnl_dr:>12,.2f}  Cr={total_jnl_cr:>12,.2f}")

        # ── Show aggregated view ──
        agg = OrderedDict()
        for jl in jnl_lines:
            key = jl.account_code
            if key not in agg:
                agg[key] = {"name": jl.account_name, "dr": Decimal("0"), "cr": Decimal("0")}
            agg[key]["dr"] += jl.debit
            agg[key]["cr"] += jl.credit

        self.stdout.write(f"\n{'─' * 70}")
        self.stdout.write(f"AGGREGATED BY ACCOUNT CODE ({len(agg)} unique codes from {len(jnl_lines)} lines):")
        for code, vals in agg.items():
            self.stdout.write(
                f"  {code:<10} {vals['name']:<30} Dr={vals['dr']:>12,.2f}  Cr={vals['cr']:>12,.2f}"
            )

        # ── Show current TB lines (FK-linked) ──
        tb_fk_lines = list(TrialBalanceLine.objects.filter(
            source_journal=journal,
            financial_year=fy,
        ).order_by("account_code"))

        self.stdout.write(f"\n{'─' * 70}")
        self.stdout.write(f"CURRENT TB LINES (source_journal FK): {len(tb_fk_lines)}")
        tb_fk_dr = Decimal("0")
        tb_fk_cr = Decimal("0")
        for tb in tb_fk_lines:
            self.stdout.write(
                f"  {tb.account_code:<10} {tb.account_name:<30} "
                f"Dr={tb.debit:>12,.2f}  Cr={tb.credit:>12,.2f}  "
                f"closing={tb.closing_balance:>12,.2f}  pk={tb.pk}"
            )
            tb_fk_dr += tb.debit
            tb_fk_cr += tb.credit
        self.stdout.write(f"  {'':>42} Dr={tb_fk_dr:>12,.2f}  Cr={tb_fk_cr:>12,.2f}")

        # ── Show orphaned TB lines (matching but no FK) ──
        orphan_count = 0
        for code, vals in agg.items():
            orphans = TrialBalanceLine.objects.filter(
                financial_year=fy,
                account_code=code,
                is_adjustment=True,
                source="manual_journal",
                source_journal__isnull=True,
                bulk_journal_upload__isnull=True,
            )
            for orph in orphans:
                if orphan_count == 0:
                    self.stdout.write(f"\n{'─' * 70}")
                    self.stdout.write("ORPHANED TB LINES (manual_journal, no FK, matching account codes):")
                self.stdout.write(
                    f"  {orph.account_code:<10} {orph.account_name:<30} "
                    f"Dr={orph.debit:>12,.2f}  Cr={orph.credit:>12,.2f}  pk={orph.pk}"
                )
                orphan_count += 1
        if orphan_count:
            self.stdout.write(f"  Total orphans: {orphan_count}")

        # ── Diagnosis ──
        self.stdout.write(f"\n{'─' * 70}")
        expected_count = len(agg)
        value_match = (tb_fk_dr == total_jnl_dr and tb_fk_cr == total_jnl_cr)

        if len(tb_fk_lines) == expected_count and value_match:
            self.stdout.write(self.style.SUCCESS(
                f"DIAGNOSIS: TB lines look correct ({expected_count} lines, values match)."
            ))
            if not dry_run:
                self.stdout.write("No repair needed. Use --dry-run to inspect without changes.")
                return
        else:
            issues = []
            if len(tb_fk_lines) != expected_count:
                issues.append(
                    f"COUNT: expected {expected_count} TB lines (unique account codes), "
                    f"found {len(tb_fk_lines)} FK-linked"
                )
            if not value_match:
                issues.append(
                    f"VALUES: JNL Dr={total_jnl_dr} Cr={total_jnl_cr} "
                    f"vs TB Dr={tb_fk_dr} Cr={tb_fk_cr}"
                )
            for issue in issues:
                self.stdout.write(self.style.ERROR(f"DIAGNOSIS: {issue}"))

        if dry_run:
            self.stdout.write(self.style.WARNING(
                "\n[DRY RUN] Would delete all FK-linked and orphan TB lines, "
                f"then re-post {expected_count} aggregated TB line(s)."
            ))
            return

        # ── Repair ──
        self.stdout.write(f"\n{'─' * 70}")
        self.stdout.write("REPAIRING...")

        from core.views import _post_journal_to_tb

        with transaction.atomic():
            # Delete FK-linked TB lines
            deleted_fk = TrialBalanceLine.objects.filter(
                source_journal=journal,
            ).delete()[0]
            self.stdout.write(f"  Deleted {deleted_fk} FK-linked TB line(s)")

            # Delete orphaned unlinked matches (aggregated values)
            deleted_orphans = 0
            for code, vals in agg.items():
                result = TrialBalanceLine.objects.filter(
                    financial_year=fy,
                    account_code=code,
                    debit=vals["dr"],
                    credit=vals["cr"],
                    is_adjustment=True,
                    source="manual_journal",
                    source_journal__isnull=True,
                    bulk_journal_upload__isnull=True,
                ).delete()
                deleted_orphans += result[0]

            # Also delete orphans matching individual (non-aggregated) values
            # in case the original posting was one-per-line
            for jl in jnl_lines:
                result = TrialBalanceLine.objects.filter(
                    financial_year=fy,
                    account_code=jl.account_code,
                    debit=jl.debit,
                    credit=jl.credit,
                    is_adjustment=True,
                    source="manual_journal",
                    source_journal__isnull=True,
                    bulk_journal_upload__isnull=True,
                ).delete()
                deleted_orphans += result[0]
            self.stdout.write(f"  Deleted {deleted_orphans} orphaned TB line(s)")

            # Re-post with aggregation
            _post_journal_to_tb(journal, fy)

        # ── Verify result ──
        new_tb_lines = list(TrialBalanceLine.objects.filter(
            source_journal=journal,
            financial_year=fy,
        ).order_by("account_code"))

        self.stdout.write(f"\n{'─' * 70}")
        self.stdout.write(f"RESULT: {len(new_tb_lines)} TB lines created:")
        new_dr = Decimal("0")
        new_cr = Decimal("0")
        for tb in new_tb_lines:
            self.stdout.write(
                f"  {tb.account_code:<10} {tb.account_name:<30} "
                f"Dr={tb.debit:>12,.2f}  Cr={tb.credit:>12,.2f}  pk={tb.pk}"
            )
            new_dr += tb.debit
            new_cr += tb.credit
        self.stdout.write(f"  {'':>42} Dr={new_dr:>12,.2f}  Cr={new_cr:>12,.2f}")

        if new_dr == total_jnl_dr and new_cr == total_jnl_cr:
            self.stdout.write(self.style.SUCCESS("\nRepair successful — TB values match journal totals."))
        else:
            self.stdout.write(self.style.ERROR(
                f"\nWARNING: TB totals (Dr={new_dr}, Cr={new_cr}) do not match "
                f"journal totals (Dr={total_jnl_dr}, Cr={total_jnl_cr})!"
            ))
