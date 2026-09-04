"""Recompute AdjustingJournal.total_debit / total_credit from their own lines.

``journal_edit`` loaded the journal with ``prefetch_related("lines")`` and then
read ``journal.lines.all()`` again after the formset save. Django serves a bare
``.all()`` on a related manager out of the prefetch cache, so the cached header
totals were written from the *pre-edit* rows. The lines and the trial balance
were always correct -- only the two cached columns drifted, and they are what
the journal screen prints at the bottom and what ``is_balanced`` reads.

The code fix is in core/views.py (the prefetch cache is dropped after the
formset save). This repairs the rows that drifted before it landed.

Purely derived: it recomputes a cache from the rows it is a cache of. No
trial balance line, journal line or financial statement figure is touched, so
a finalised year's statements are unaffected -- the figures they were built
from are the lines, which already agree.

Journals with NO lines at all are reported and skipped, never zeroed: a cached
total with no lines behind it (Hazaway 2024 JE-005) came from a different
route and needs its own decision.

Usage:
    python3 data_fixes/repair_stale_journal_cached_totals.py --dry-run
    python3 data_fixes/repair_stale_journal_cached_totals.py --commit
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone as dt_timezone
from decimal import Decimal

import django

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.db import transaction  # noqa: E402
from django.db.models import Sum  # noqa: E402

from core.models import AdjustingJournal  # noqa: E402

ZERO = Decimal("0.00")


def find_drift():
    """Return (repairable, no_lines) lists of (journal, line_dr, line_cr)."""
    repairable, no_lines = [], []
    qs = (
        AdjustingJournal.objects
        .annotate(ldr=Sum("lines__debit"), lcr=Sum("lines__credit"))
        .select_related("financial_year", "financial_year__entity")
        .order_by("financial_year__entity__entity_name", "reference_number")
    )
    for j in qs:
        if j.ldr is None and j.lcr is None:
            if j.total_debit != ZERO or j.total_credit != ZERO:
                no_lines.append((j, ZERO, ZERO))
            continue
        ldr, lcr = j.ldr or ZERO, j.lcr or ZERO
        if j.total_debit != ldr or j.total_credit != lcr:
            repairable.append((j, ldr, lcr))
    return repairable, no_lines


def describe(j, ldr, lcr):
    e = j.financial_year.entity
    return (
        f"  {e.entity_name} {j.financial_year.year_label} {j.reference_number} "
        f"[{j.status}]\n"
        f"      cached Dr {j.total_debit:,} Cr {j.total_credit:,}"
        f"  ->  lines Dr {ldr:,} Cr {lcr:,}"
    )


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--commit", action="store_true")
    args = ap.parse_args()

    repairable, no_lines = find_drift()

    print(f"Journals whose cached totals disagree with their lines: {len(repairable)}")
    for row in repairable:
        print(describe(*row))

    if no_lines:
        print(f"\nSKIPPED -- cached totals but no journal lines ({len(no_lines)}).")
        print("These did not come from the edit path; decide each one by hand:")
        for row in no_lines:
            print(describe(*row))

    if not repairable:
        print("\nNothing to repair.")
        return

    if args.dry_run:
        print("\nDry run -- nothing written. Re-run with --commit to apply.")
        return

    stamp = datetime.now(dt_timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        f"journal_cached_totals_pre_repair_{stamp}.json",
    )
    backup = [
        {
            "journal_id": str(j.pk),
            "entity": j.financial_year.entity.entity_name,
            "year_label": j.financial_year.year_label,
            "reference_number": j.reference_number,
            "total_debit": str(j.total_debit),
            "total_credit": str(j.total_credit),
        }
        for j, _, _ in repairable
    ]
    with open(backup_path, "w") as fh:
        json.dump(backup, fh, indent=2)
    print(f"\nBacked up prior values to {backup_path}")

    with transaction.atomic():
        for j, ldr, lcr in repairable:
            # recalculate_totals() re-aggregates from the database and saves
            # only the two cached columns, so updated_at is the only other
            # field that moves.
            j.recalculate_totals()

    print(f"Repaired {len(repairable)} journal(s).")


if __name__ == "__main__":
    main()
