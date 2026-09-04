"""Mark Kinross's opening accumulated depreciation as carried forward, not earned.

The Depreciation "Post to Trial Balance" button refuses on Kinross FY2025:

    2895 Less: Accumulated depreciation has $4,373.00 of current-year movement
    against $0.00 on 1617 Depreciation - Other

It is right to refuse. The button reverses the year's movement in the account
pair and re-posts the schedule total, and those two sides do not carry equal and
opposite movement, so the reversal would write an unbalanced journal. Worse, the
4,373.00 it would back out is not this year's depreciation at all -- it is FY2024's
accumulated depreciation, journalled in by JE-001 as the opening balance sheet
when the client was onboarded. Reversing it would erase the opening position.

The guard excludes lines tagged source="rollover" -- the vocabulary this codebase
uses for "carried in from the prior year" -- and JE-001 wrote source="manual_journal"
instead, with the amount in `credit` rather than `opening_balance`. Nothing about
the row says it is an opening balance; JE-001 is even dated 30 June 2025, the last
day of the year. So the reconciliation reads it as movement earned this year.

Retagging says what is true: this balance was carried forward. It is also exactly
what the button's own error message asks for, though nothing in the application can
currently do it -- "retag" appears once in the codebase, in that message.

Checked before writing, because `source` is read in four places:

    reroll_forward's wipe        filters is_adjustment=False; these rows are True,
                                 so they are preserved either way.
    comparatives (views 3164,    filter source="rollover" AND is_adjustment=False,
    3233)                        so these rows are excluded either way.
    TB display (views 2098)      sets _cy from closing_balance on both branches and
                                 _py from prior_debit/credit, which are 0.00 here.
                                 The screen does not change.
    the depreciation guard       stops seeing an opening balance as movement, which
    (views 2437, 2443)           is the point.

Posting afterwards debits 1617 and credits 2895 with 15,933.75, leaving accumulated
depreciation at 20,306.75 -- the 4,373.00 brought forward plus this year's charge.

Does NOT post the depreciation. That stays a decision made at the button, with the
preview and the audit trail that come with it.

Usage:
    python3 data_fixes/retag_kinross_opening_accum_depreciation.py --dry-run
    python3 data_fixes/retag_kinross_opening_accum_depreciation.py --commit
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

from core.models import Entity, TrialBalanceLine  # noqa: E402

# Only the account that blocks the post. 2869 carries the same shape -- 34,081.00
# of opening accumulated depreciation against fully written-down plant -- but no
# asset charges depreciation to it, so it is not in the resolved account group and
# does not block anything today. Left alone rather than swept up.
BLOCKING_ACCOUNT = "2895"
ZERO = Decimal("0.00")


def target_line():
    entity = Entity.objects.filter(entity_name__icontains="kinross").first()
    if entity is None:
        raise SystemExit("No entity matching 'kinross'.")
    fy = entity.financial_years.filter(year_label="2025").first()
    if fy is None:
        raise SystemExit("Kinross has no FY2025.")
    if fy.is_locked:
        raise SystemExit(f"FY2025 is {fy.status} and locked.")
    rows = list(TrialBalanceLine.objects.filter(
        financial_year=fy, account_code=BLOCKING_ACCOUNT))
    if len(rows) != 1:
        raise SystemExit(
            f"Expected exactly one {BLOCKING_ACCOUNT} line on FY2025, found "
            f"{len(rows)}. Look before writing."
        )
    return fy, rows[0]


def guard_state(fy, dep_code="1617", accum_code=BLOCKING_ACCOUNT):
    """What the post-to-TB reconciliation sees for this account pair."""
    dm = TrialBalanceLine.objects.filter(
        financial_year=fy, account_code=dep_code,
    ).exclude(source="rollover").aggregate(dr=Sum("debit"), cr=Sum("credit"))
    am = TrialBalanceLine.objects.filter(
        financial_year=fy, account_code=accum_code,
    ).exclude(source="rollover").aggregate(dr=Sum("debit"), cr=Sum("credit"))
    dep_net = (dm["dr"] or ZERO) - (dm["cr"] or ZERO)
    accum_net_cr = (am["cr"] or ZERO) - (am["dr"] or ZERO)
    return dep_net, accum_net_cr


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--commit", action="store_true")
    args = ap.parse_args()

    fy, line = target_line()

    if line.source == "rollover":
        print("Already tagged as a rollover line. Nothing to do.")
        return

    before = dep_net, accum_net = guard_state(fy)
    print(f"Kinross Builders FY2025 · TB line {line.account_code} {line.account_name}")
    print(f"  source          {line.source!r} -> 'rollover'")
    print(f"  credit          {line.credit:,}   (unchanged)")
    print(f"  closing_balance {line.closing_balance:,}   (unchanged)")
    print(f"  is_adjustment   {line.is_adjustment}   (unchanged)")
    print(f"  source_journal  {line.source_journal}")
    print(f"\n  post-to-TB reconciliation BEFORE: "
          f"1617 movement {dep_net:,} vs 2895 movement {accum_net:,}  -> BLOCKED")

    if args.dry_run:
        print("\nDry run — nothing written. Re-run with --commit to apply.")
        return

    stamp = datetime.now(dt_timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        f"kinross_fy2025_2895_pre_retag_{stamp}.json",
    )
    with open(path, "w") as fh:
        json.dump({
            "trial_balance_line_id": str(line.pk),
            "financial_year_id": str(fy.pk),
            "account_code": line.account_code,
            "account_name": line.account_name,
            "source_before": line.source,
            "credit": str(line.credit),
            "closing_balance": str(line.closing_balance),
            "is_adjustment": line.is_adjustment,
        }, fh, indent=2)
    print(f"\nBacked up prior value to {path}")

    with transaction.atomic():
        TrialBalanceLine.objects.filter(pk=line.pk).update(source="rollover")

    dep_net, accum_net = guard_state(fy)
    verdict = "CLEARS" if dep_net == accum_net else "STILL BLOCKED"
    print(f"  post-to-TB reconciliation AFTER : "
          f"1617 movement {dep_net:,} vs 2895 movement {accum_net:,}  -> {verdict}")
    print("\nApplied. Press Post to Trial Balance on the Depreciation tab to post "
          "15,933.75; the preview will show the net adjustment first.")


if __name__ == "__main__":
    main()
