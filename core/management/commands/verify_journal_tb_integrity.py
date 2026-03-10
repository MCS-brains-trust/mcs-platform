"""
Management command to verify and repair journal-to-TB integrity.

For every posted journal across all entities and financial years, checks
whether its debit and credit lines are correctly reflected in the trial
balance.  Identifies journals with missing or incorrect TB impact and
optionally repairs them by re-posting the missing lines.

Usage:
    python manage.py verify_journal_tb_integrity              # report only
    python manage.py verify_journal_tb_integrity --repair      # report + fix
    python manage.py verify_journal_tb_integrity --entity "Foo" # filter by entity
"""
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import (
    AdjustingJournal,
    TrialBalanceLine,
)


class Command(BaseCommand):
    help = "Verify every posted journal has matching TB lines and repair any gaps"

    def add_arguments(self, parser):
        parser.add_argument(
            "--repair",
            action="store_true",
            help="Re-post TB lines for journals with missing or incorrect TB impact",
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
        repair = options["repair"]
        entity_filter = options.get("entity")
        journal_uuid = options.get("journal")

        journals_qs = AdjustingJournal.objects.filter(
            status=AdjustingJournal.JournalStatus.POSTED,
        ).select_related(
            "financial_year", "financial_year__entity",
        ).prefetch_related("lines")

        if journal_uuid:
            journals_qs = journals_qs.filter(pk=journal_uuid)
        elif entity_filter:
            journals_qs = journals_qs.filter(
                financial_year__entity__entity_name__icontains=entity_filter
            )

        total_checked = 0
        total_ok = 0
        total_missing = 0
        total_count_mismatch = 0
        total_value_mismatch = 0
        total_repaired = 0

        for journal in journals_qs.iterator(chunk_size=100):
            total_checked += 1
            fy = journal.financial_year
            entity_name = fy.entity.entity_name
            jnl_lines = list(journal.lines.all())

            if not jnl_lines:
                continue

            # TB lines linked via FK
            tb_lines = list(TrialBalanceLine.objects.filter(
                source_journal=journal,
                financial_year=fy,
                is_adjustment=True,
            ))

            expected_count = len(jnl_lines)
            actual_count = len(tb_lines)

            # Calculate expected totals from journal lines
            expected_dr = sum(l.debit for l in jnl_lines)
            expected_cr = sum(l.credit for l in jnl_lines)

            # Calculate actual totals from TB lines
            actual_dr = sum(l.debit for l in tb_lines)
            actual_cr = sum(l.credit for l in tb_lines)

            # Determine the type of mismatch
            if actual_count == 0:
                # Completely missing — no FK-linked TB lines at all
                # Try to find unlinked TB lines that might belong to this journal
                unlinked_count = 0
                for jl in jnl_lines:
                    exists = TrialBalanceLine.objects.filter(
                        financial_year=fy,
                        account_code=jl.account_code,
                        debit=jl.debit,
                        credit=jl.credit,
                        is_adjustment=True,
                        source="manual_journal",
                        source_journal__isnull=True,
                        bulk_journal_upload__isnull=True,
                    ).exists()
                    if exists:
                        unlinked_count += 1

                if unlinked_count == expected_count:
                    # All lines exist but are unlinked — backfill FKs
                    self.stdout.write(
                        self.style.WARNING(
                            f"  {entity_name} / {fy.year_label} — "
                            f"{journal.reference_number} ({journal.pk}): "
                            f"{expected_count} TB lines exist but source_journal FK is NULL"
                        )
                    )
                    if repair:
                        with transaction.atomic():
                            linked = 0
                            for jl in jnl_lines:
                                tb_line = TrialBalanceLine.objects.filter(
                                    financial_year=fy,
                                    account_code=jl.account_code,
                                    debit=jl.debit,
                                    credit=jl.credit,
                                    is_adjustment=True,
                                    source="manual_journal",
                                    source_journal__isnull=True,
                                    bulk_journal_upload__isnull=True,
                                ).first()
                                if tb_line:
                                    tb_line.source_journal = journal
                                    tb_line.save(update_fields=["source_journal"])
                                    linked += 1
                        self.stdout.write(
                            self.style.SUCCESS(
                                f"    → Backfilled source_journal FK on {linked} TB line(s)"
                            )
                        )
                        total_repaired += 1
                    total_missing += 1
                elif unlinked_count > 0:
                    # Partial match — some lines exist unlinked
                    self.stdout.write(
                        self.style.WARNING(
                            f"  {entity_name} / {fy.year_label} — "
                            f"{journal.reference_number} ({journal.pk}): "
                            f"PARTIAL: {unlinked_count}/{expected_count} unlinked TB lines found, "
                            f"{expected_count - unlinked_count} completely missing"
                        )
                    )
                    if repair:
                        self._repair_journal(journal, fy, jnl_lines)
                        total_repaired += 1
                    total_missing += 1
                else:
                    # Completely missing — no TB lines at all
                    self.stdout.write(
                        self.style.ERROR(
                            f"  {entity_name} / {fy.year_label} — "
                            f"{journal.reference_number} ({journal.pk}): "
                            f"MISSING: 0/{expected_count} TB lines found — "
                            f"expected Dr={expected_dr} Cr={expected_cr}"
                        )
                    )
                    if repair:
                        self._repair_journal(journal, fy, jnl_lines)
                        total_repaired += 1
                    total_missing += 1

            elif actual_count != expected_count:
                self.stdout.write(
                    self.style.WARNING(
                        f"  {entity_name} / {fy.year_label} — "
                        f"{journal.reference_number} ({journal.pk}): "
                        f"COUNT MISMATCH: expected {expected_count} TB lines, "
                        f"found {actual_count}"
                    )
                )
                if repair:
                    self._repair_journal(journal, fy, jnl_lines)
                    total_repaired += 1
                total_count_mismatch += 1

            elif actual_dr != expected_dr or actual_cr != expected_cr:
                self.stdout.write(
                    self.style.WARNING(
                        f"  {entity_name} / {fy.year_label} — "
                        f"{journal.reference_number} ({journal.pk}): "
                        f"VALUE MISMATCH: JNL Dr={expected_dr} Cr={expected_cr} "
                        f"vs TB Dr={actual_dr} Cr={actual_cr}"
                    )
                )
                if repair:
                    self._repair_journal(journal, fy, jnl_lines)
                    total_repaired += 1
                total_value_mismatch += 1

            else:
                total_ok += 1

        # ── Summary ──────────────────────────────────────────────────────
        total_issues = total_missing + total_count_mismatch + total_value_mismatch
        self.stdout.write("")
        self.stdout.write("=" * 60)
        self.stdout.write(f"  Journals checked:       {total_checked}")
        self.stdout.write(self.style.SUCCESS(f"  OK (correct TB impact): {total_ok}"))
        if total_missing:
            self.stdout.write(self.style.ERROR(f"  Missing TB lines:       {total_missing}"))
        if total_count_mismatch:
            self.stdout.write(self.style.WARNING(f"  Count mismatches:       {total_count_mismatch}"))
        if total_value_mismatch:
            self.stdout.write(self.style.WARNING(f"  Value mismatches:       {total_value_mismatch}"))
        if repair and total_repaired:
            self.stdout.write(self.style.SUCCESS(f"  Repaired:               {total_repaired}"))
        elif total_issues and not repair:
            self.stdout.write(
                self.style.WARNING(f"\n  {total_issues} issue(s) found. Use --repair to fix them.")
            )
        self.stdout.write("=" * 60)

        if total_issues == 0:
            self.stdout.write(self.style.SUCCESS("\nAll posted journals have correct TB impact."))

    def _repair_journal(self, journal, fy, jnl_lines):
        """Delete existing FK-linked TB lines and re-post from journal lines."""
        from core.views import _apply_journal_line_to_tb

        with transaction.atomic():
            # Delete all existing TB lines for this journal (FK-linked)
            deleted_fk = TrialBalanceLine.objects.filter(
                source_journal=journal,
            ).delete()[0]

            # Also clean up unlinked manual_journal lines that match exactly
            # (pre-FK orphans for this specific journal)
            deleted_unlinked = 0
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
                deleted_unlinked += result[0]

            # Re-post each journal line to TB with source_journal FK
            for jl in jnl_lines:
                _apply_journal_line_to_tb(
                    fy, jl.account_code, jl.account_name,
                    jl.debit, jl.credit, source="manual_journal",
                    description=journal.description,
                    journal=journal,
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"    → Repaired: deleted {deleted_fk} FK-linked + "
                f"{deleted_unlinked} unlinked TB line(s), "
                f"re-posted {len(jnl_lines)} line(s) with source_journal FK"
            )
        )
