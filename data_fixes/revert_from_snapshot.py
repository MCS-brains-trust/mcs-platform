"""
Revert a PendingTransaction data repair from its JSON snapshot.

The repair scripts in this directory write a snapshot of the prior field values
before changing anything. This restores them.

Usage
-----
    SNAPSHOT=data_fixes/<name>_snapshot.json \
        python3 manage.py shell < data_fixes/revert_from_snapshot.py          # dry run

    SNAPSHOT=data_fixes/<name>_snapshot.json APPLY=1 \
        python3 manage.py shell < data_fixes/revert_from_snapshot.py          # apply

Snapshots hold live client transaction detail and are deliberately not committed
to the repository — they exist only on the server that ran the repair.
"""
import json
import os
from decimal import Decimal

from django.db import transaction as db_transaction

from review.models import PendingTransaction

APPLY = os.environ.get("APPLY") == "1"
SNAPSHOT = os.environ.get("SNAPSHOT")
assert SNAPSHOT, "set SNAPSHOT=<path to snapshot json>"

# Fields a repair script may have changed. Only those present in the snapshot
# are restored, so a snapshot that captured fewer fields still works.
RESTORABLE = {
    "confirmed_tax_type": str,
    "ai_suggested_tax_type": str,
    "gst_treatment": str,
    "is_gst_manual": bool,
    "creditable_percentage": Decimal,
    "confirmed_gst_amount": Decimal,
    "gst_amount": Decimal,
    "net_amount": Decimal,
}

rows = json.load(open(SNAPSHOT))
print(f"{len(rows)} rows in {SNAPSHOT}")

planned = []
for row in rows:
    try:
        txn = PendingTransaction.objects.get(pk=row["id"])
    except PendingTransaction.DoesNotExist:
        print(f"   MISSING {row['id']} — skipped")
        continue

    changes = {}
    for field, cast in RESTORABLE.items():
        if field not in row:
            continue
        want = cast(row[field]) if row[field] is not None else None
        if getattr(txn, field) != want:
            changes[field] = want
    if changes:
        planned.append((txn, changes))

print(f"{len(planned)} rows differ from the snapshot and would be restored")
for txn, changes in planned[:40]:
    summary = ", ".join(f"{k}: {getattr(txn, k)!r} -> {v!r}" for k, v in changes.items())
    print(f"   {txn.date:<12} {abs(txn.amount):>10}  {summary}")

if not APPLY:
    print("\nDRY RUN — nothing written. Re-run with APPLY=1 to apply.")
else:
    with db_transaction.atomic():
        for txn, changes in planned:
            for field, value in changes.items():
                setattr(txn, field, value)
            txn.save(update_fields=list(changes))
    print(f"REVERTED {len(planned)} transactions.")
