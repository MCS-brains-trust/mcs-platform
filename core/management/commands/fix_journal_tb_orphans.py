"""
Management command to find and repair orphaned journal TB lines.

Detects two categories of problems:

  1. **Unlinked TB lines** — posted journals whose TrialBalanceLine
     adjustment rows have source_journal=NULL (posted before the FK was
     added in migration 0068).  Fix: backfill the source_journal FK.

  2. **Orphaned TB lines** — adjustment TB lines with source='manual_journal'
     and source_journal=NULL that no longer correspond to any existing
     journal.  These are left behind by failed deletions or partially-
     applied edits.  Fix: delete them (with --delete-orphans flag).

Usage:
    python manage.py fix_journal_tb_orphans --dry-run
    python manage.py fix_journal_tb_orphans
    python manage.py fix_journal_tb_orphans --delete-orphans
    python manage.py fix_journal_tb_orphans --entity "Entity Name"
    python manage.py fix_journal_tb_orphans --journal <uuid>
"""
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import (
    AdjustingJournal,
    Entity,
    FinancialYear,
    JournalLine,
    TrialBalanceLine,
)


class Command(BaseCommand):
    help = "Find and repair orphaned journal TB lines (source_journal not set)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report problems without making changes",
        )
        parser.add_argument(
            "--delete-orphans",
            action="store_true",
            help="Delete adjustment TB lines that don't match any journal",
        )
        parser.add_argument(
            "--entity",
            type=str,
            help="Limit to a specific entity by name (substring match)",
        )
        parser.add_argument(
            "--journal",
            type=str,
            help="Limit to a specific journal by UUID",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        delete_orphans = options["delete_orphans"]
        entity_filter = options.get("entity")
        journal_uuid = options.get("journal")

        if dry_run:
            self.stdout.write(self.style.WARNING("=== DRY RUN — no changes will be made ===\n"))

        # ── Phase 1: Backfill source_journal on unlinked TB lines ─────
        self.stdout.write(self.style.HTTP_INFO("Phase 1: Backfill source_journal FK on unlinked TB lines"))

        journals_qs = AdjustingJournal.objects.filter(
            status=AdjustingJournal.JournalStatus.POSTED,
        ).select_related("financial_year", "financial_year__entity").prefetch_related("lines")

        if journal_uuid:
            journals_qs = journals_qs.filter(pk=journal_uuid)
        elif entity_filter:
            journals_qs = journals_qs.filter(
                financial_year__entity__entity_name__icontains=entity_filter
            )

        total_linked = 0
        total_journals_fixed = 0

        for journal in journals_qs.iterator(chunk_size=100):
            fy = journal.financial_year
            entity_name = fy.entity.entity_name

            # Skip journals that already have FK-linked TB lines
            if journal.tb_lines.exists():
                continue

            # Try to match unlinked TB lines to this journal's lines
            journal_lines = list(journal.lines.all())
            if not journal_lines:
                continue

            matched = []
            for jnl_line in journal_lines:
                candidates = TrialBalanceLine.objects.filter(
                    financial_year=fy,
                    account_code=jnl_line.account_code,
                    debit=jnl_line.debit,
                    credit=jnl_line.credit,
                    is_adjustment=True,
                    source_journal__isnull=True,
                )
                candidate = candidates.first()
                if candidate:
                    matched.append(candidate)

            if not matched:
                # Try broader match: account_code + source type only
                for jnl_line in journal_lines:
                    candidates = TrialBalanceLine.objects.filter(
                        financial_year=fy,
                        account_code=jnl_line.account_code,
                        is_adjustment=True,
                        source__in=("manual_journal", "journal_upload"),
                        source_journal__isnull=True,
                    )
                    candidate = candidates.first()
                    if candidate and candidate not in matched:
                        matched.append(candidate)

            if matched:
                self.stdout.write(
                    f"  {entity_name} / {fy.year_label} — "
                    f"journal {journal.reference_number} ({journal.pk}): "
                    f"linking {len(matched)} TB line(s)"
                )
                if not dry_run:
                    with transaction.atomic():
                        for tb_line in matched:
                            tb_line.source_journal = journal
                            tb_line.save(update_fields=["source_journal"])
                total_linked += len(matched)
                total_journals_fixed += 1

        self.stdout.write(
            f"\n  Phase 1 summary: {total_journals_fixed} journal(s), "
            f"{total_linked} TB line(s) {'would be ' if dry_run else ''}linked\n"
        )

        # ── Phase 2: Find truly orphaned TB lines ─────────────────────
        self.stdout.write(self.style.HTTP_INFO("Phase 2: Find orphaned adjustment TB lines"))

        orphan_qs = TrialBalanceLine.objects.filter(
            is_adjustment=True,
            source__in=("manual_journal", "journal_upload"),
            source_journal__isnull=True,
        ).select_related("financial_year", "financial_year__entity")

        if entity_filter:
            orphan_qs = orphan_qs.filter(
                financial_year__entity__entity_name__icontains=entity_filter
            )

        # Group by FY for reporting
        orphans_by_fy = {}
        for line in orphan_qs:
            key = (
                line.financial_year.entity.entity_name,
                line.financial_year.year_label,
                line.financial_year_id,
            )
            orphans_by_fy.setdefault(key, []).append(line)

        total_orphans = 0
        for (entity_name, year_label, fy_id), lines in sorted(orphans_by_fy.items()):
            # Check if any of these lines could belong to a known journal
            # (i.e. a journal whose lines match but wasn't caught in Phase 1)
            fy_journals = AdjustingJournal.objects.filter(
                financial_year_id=fy_id,
                status=AdjustingJournal.JournalStatus.POSTED,
            ).prefetch_related("lines")

            claimed_ids = set()
            for journal in fy_journals:
                jnl_codes = set(journal.lines.values_list("account_code", flat=True))
                for line in lines:
                    if line.account_code in jnl_codes and line.pk not in claimed_ids:
                        claimed_ids.add(line.pk)

            truly_orphaned = [l for l in lines if l.pk not in claimed_ids]
            if truly_orphaned:
                self.stdout.write(
                    f"  {entity_name} / {year_label}: "
                    f"{len(truly_orphaned)} orphaned TB line(s)"
                )
                for line in truly_orphaned[:5]:  # Show first 5
                    self.stdout.write(
                        f"    {line.account_code} {line.account_name}: "
                        f"Dr {line.debit} Cr {line.credit} "
                        f"(source={line.source}, pk={line.pk})"
                    )
                if len(truly_orphaned) > 5:
                    self.stdout.write(f"    ... and {len(truly_orphaned) - 5} more")
                total_orphans += len(truly_orphaned)

                if delete_orphans and not dry_run:
                    pks = [l.pk for l in truly_orphaned]
                    deleted = TrialBalanceLine.objects.filter(pk__in=pks).delete()[0]
                    self.stdout.write(
                        self.style.SUCCESS(f"    → Deleted {deleted} orphaned line(s)")
                    )

        self.stdout.write(
            f"\n  Phase 2 summary: {total_orphans} orphaned TB line(s) found"
        )
        if total_orphans and not delete_orphans:
            self.stdout.write(
                self.style.WARNING("  Use --delete-orphans to remove them")
            )

        # ── Phase 3: Integrity check — journals with wrong TB impact ──
        self.stdout.write(self.style.HTTP_INFO(
            "\nPhase 3: Journals with mismatched TB impact"
        ))

        journals_qs2 = AdjustingJournal.objects.filter(
            status=AdjustingJournal.JournalStatus.POSTED,
        ).select_related("financial_year", "financial_year__entity").prefetch_related("lines")

        if journal_uuid:
            journals_qs2 = journals_qs2.filter(pk=journal_uuid)
        elif entity_filter:
            journals_qs2 = journals_qs2.filter(
                financial_year__entity__entity_name__icontains=entity_filter
            )

        mismatch_count = 0
        for journal in journals_qs2.iterator(chunk_size=100):
            jnl_lines = list(journal.lines.all())
            tb_lines = list(journal.tb_lines.all())
            expected_count = len(jnl_lines)
            actual_count = len(tb_lines)

            if expected_count == 0:
                continue

            # Check count mismatch
            if expected_count != actual_count:
                fy = journal.financial_year
                self.stdout.write(
                    self.style.WARNING(
                        f"  {fy.entity.entity_name} / {fy.year_label} — "
                        f"journal {journal.reference_number} ({journal.pk}): "
                        f"expected {expected_count} TB lines, found {actual_count}"
                    )
                )
                mismatch_count += 1
                continue

            # Check value mismatch (total debit/credit)
            jnl_dr = sum(l.debit for l in jnl_lines)
            jnl_cr = sum(l.credit for l in jnl_lines)
            tb_dr = sum(l.debit for l in tb_lines)
            tb_cr = sum(l.credit for l in tb_lines)

            if jnl_dr != tb_dr or jnl_cr != tb_cr:
                fy = journal.financial_year
                self.stdout.write(
                    self.style.WARNING(
                        f"  {fy.entity.entity_name} / {fy.year_label} — "
                        f"journal {journal.reference_number} ({journal.pk}): "
                        f"JNL totals Dr={jnl_dr} Cr={jnl_cr} vs "
                        f"TB totals Dr={tb_dr} Cr={tb_cr}"
                    )
                )
                mismatch_count += 1

        self.stdout.write(
            f"\n  Phase 3 summary: {mismatch_count} journal(s) with mismatched TB impact"
        )

        # ── Done ──────────────────────────────────────────────────────
        if dry_run:
            self.stdout.write(self.style.WARNING("\n=== DRY RUN complete — re-run without --dry-run to apply fixes ==="))
        else:
            self.stdout.write(self.style.SUCCESS("\nDone."))
