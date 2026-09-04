"""Open Kinross Builders' Range Rover at its written-down value, not its cost.

Kinross FY2025 charged 17,027.00 of depreciation on the Range Rover where
HandiLedger charged 15,934. The schedule was applying 25% diminishing value to
68,108.00 -- the car limit, i.e. the full cost -- because the asset's opening
written-down value had been recorded as cost.

The trial balance already knew better. FY2025 carries 4,373.00 of accumulated
depreciation at account 2895, journalled in by hand because FY2024 was finalised
with no depreciation schedule at all, so nothing rolled forward. Cost less that
accumulated depreciation is 63,735.00, and 25% of it is 15,933.75 -- HandiLedger's
15,934, which is the same figure in whole dollars (the entity has show_cents off,
so the statements print it as 15,934 either way).

This corrects the one asset. It does NOT invent the missing FY2024 schedule, and
it does not touch the trial balance: FY2025's depreciation has not been posted
there yet, so the schedule and the ledger do not disagree once this lands.

Sets opening_wdv and re-derives everything else through _calc_depreciation, so
depreciable_value, depreciation_amount and closing_wdv move together rather than
one column being patched out from under the others.

Usage:
    python3 data_fixes/fix_kinross_fy2025_range_rover_opening_wdv.py --dry-run
    python3 data_fixes/fix_kinross_fy2025_range_rover_opening_wdv.py --commit
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

from core.models import DepreciationAsset, Entity  # noqa: E402
from core.views import _calc_depreciation  # noqa: E402

ASSET_NAME = "Range Rover AZJ923 - to dep limit"
NEW_OPENING_WDV = Decimal("63735.00")
FIELDS = ("opening_wdv", "depreciable_value", "depreciation_amount", "closing_wdv")


def find_asset():
    entity = Entity.objects.filter(entity_name__icontains="kinross").first()
    if entity is None:
        raise SystemExit("No entity matching 'kinross'.")
    fy = entity.financial_years.filter(year_label="2025").first()
    if fy is None:
        raise SystemExit("Kinross has no FY2025.")
    if fy.is_locked:
        raise SystemExit(
            f"FY2025 is {fy.status} and locked. Reopen it before correcting the "
            f"schedule, so the change carries the usual audit trail."
        )
    asset = fy.depreciation_assets.filter(asset_name=ASSET_NAME).first()
    if asset is None:
        raise SystemExit(f"No asset named {ASSET_NAME!r} on Kinross FY2025.")
    return asset


def snapshot(asset):
    return {f: str(getattr(asset, f)) for f in FIELDS}


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--commit", action="store_true")
    args = ap.parse_args()

    asset = find_asset()
    before = snapshot(asset)

    if asset.opening_wdv == NEW_OPENING_WDV:
        print(f"Opening WDV is already {NEW_OPENING_WDV}. Nothing to do.")
        return

    asset.opening_wdv = NEW_OPENING_WDV
    _calc_depreciation(asset)
    after = snapshot(asset)

    print(f"Kinross Builders FY2025 · {ASSET_NAME}")
    print(f"  method {asset.method} rate {asset.rate}%  purchased {asset.purchase_date}")
    for f in FIELDS:
        print(f"    {f:20} {before[f]:>12}  ->  {after[f]:>12}")
    print(f"\n  HandiLedger: 15,934   this schedule: {after['depreciation_amount']}")

    if args.dry_run:
        print("\nDry run — nothing written. Re-run with --commit to apply.")
        return

    stamp = datetime.now(dt_timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        f"kinross_fy2025_range_rover_pre_wdv_fix_{stamp}.json",
    )
    with open(path, "w") as fh:
        json.dump({"asset_id": str(asset.pk), "asset_name": ASSET_NAME,
                   "before": before, "after": after}, fh, indent=2)
    print(f"\nBacked up prior values to {path}")

    with transaction.atomic():
        DepreciationAsset.objects.filter(pk=asset.pk).update(
            **{f: getattr(asset, f) for f in FIELDS}
        )
    print("Applied.")


if __name__ == "__main__":
    main()
