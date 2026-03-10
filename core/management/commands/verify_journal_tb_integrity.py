"""
Management command to verify and repair journal-to-TB integrity.

For every posted journal across all entities and financial years, checks
whether its debit and credit lines are correctly reflected in the trial
balance.  Identifies journals with missing or incorrect TB impact and
optionally repairs them by re-posting the missing lines.

The posting logic aggregates multiple journal lines to the same account code
into a single TB line (summed debits and credits).  The expected TB line
count is therefore the number of *unique account codes* in the journal, not
the raw journal line count.

Usage:
    python manage.py verify_journal_tb_integrity              # report only
    python manage.py verify_journal_tb_integrity --repair      # report + fix
    python manage.py verify_journal_tb_integrity --entity "Foo" # filter by entity
"""
from collections import OrderedDict
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import (
    AdjustingJournal,
    TrialBalanceLine,
)


def _aggregate_journal_lines(jnl_lines):
    """Return an OrderedDict of {account_code: {name, dr, cr}} aggregated
    from the given journal lines.  Multiple lines to the same account code
    are summed."""
    agg = OrderedDict()
    for jl in jnl_lines:
        key = jl.account_code
        if key not in agg:
            agg[key] = {"name": jl.account_name, "dr": Decimal("0"), "cr": Decimal("0")}
        agg[key]["dr"] += jl.debit
        agg[key]["cr"] += jl.credit
    return agg


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

            # Aggregate journal lines by account code (matching posting logic)
            agg = _aggregate_journal_lines(jnl_lines)
            expected_count = len(agg)

            # TB lines linked via FK
            tb_lines = list(TrialBalanceLine.objects.filter(
                source_journal=journal,
                financial_year=fy,
                is_adjustment=True,
            ))

            actual_count = len(tb_lines)

            # Calculate expected totals from aggregated journal lines
            expected_dr = sum(v["dr"] for v in agg.values())
            expected_cr = sum(v["cr"] for v in agg.values())

            # Calculate actual totals from TB lines
            actual_dr = sum(l.debit for l in tb_lines)
            actual_cr = sum(l.credit for l in tb_lines)

            # Determine the type of mismatch
            if actual_count == 0:
                # Completely missing — no FK-linked TB lines at all.
                # Try to find unlinked TB lines that might belong to this journal
                # by checking each *aggregated* account code.
                unlinked_matches = OrderedDict()
                for code, vals in agg.items():
                    match = TrialBalanceLine.objects.filter(
                        financial_year=fy,
                        account_code=code,
                        debit=vals["dr"],
                        credit=vals["cr"],
                        is_adjustment=True,
                        source="manual_journal",
                        source_journal__isnull=True,
                        bulk_journal_upload__isnull=True,
                    ).first()
                    if match:
                        unlinked_matches[code] = match

                if len(unlinked_matches) == expected_count:
                    # All aggregated lines exist but are unlinked — backfill FKs
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
                            for code, tb_line in unlinked_matches.items():
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
                elif len(unlinked_matches) > 0:
                    # Partial match — some lines exist unlinked
                    self.stdout.write(
                        self.style.WARNING(
                            f"  {entity_name} / {fy.year_label} — "
                            f"{journal.reference_number} ({journal.pk}): "
                            f"PARTIAL: {len(unlinked_matches)}/{expected_count} unlinked TB lines found, "
                            f"{expected_count - len(unlinked_matches)} completely missing"
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
                        f"COUNT MISMATCH: expected {expected_count} TB lines "
                        f"(unique account codes), found {actual_count}"
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
        """Delete existing TB lines and re-post from journal lines using
        aggregated posting (one TB line per unique account code)."""
        from core.views import _post_journal_to_tb

        agg = _aggregate_journal_lines(jnl_lines)

        with transaction.atomic():
            # Delete all existing TB lines for this journal (FK-linked)
            deleted_fk = TrialBalanceLine.objects.filter(
                source_journal=journal,
            ).delete()[0]

            # Also clean up unlinked manual_journal lines that match the
            # aggregated values (pre-FK orphans for this specific journal).
            # Use aggregated values to avoid over-matching when multiple
            # journal lines target the same account code.
            deleted_unlinked = 0
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
                deleted_unlinked += result[0]

            # Re-post using the aggregated helper
            _post_journal_to_tb(journal, fy)

        self.stdout.write(
            self.style.SUCCESS(
                f"    → Repaired: deleted {deleted_fk} FK-linked + "
                f"{deleted_unlinked} unlinked TB line(s), "
                f"re-posted {len(agg)} aggregated TB line(s) with source_journal FK"
            )
        )
