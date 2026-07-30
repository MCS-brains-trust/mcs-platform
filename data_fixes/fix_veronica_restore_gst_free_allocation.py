"""
Data repair — Veronica Cerratti Pty Ltd FY2026: restore the GST-free allocation
that an account re-assignment overwrote.

Background
----------
38 income transactions are stored as confirmed_tax_type='GST on Income' with GST
booked, yet each carries the fingerprint of an explicit GST-free decision:

    is_gst_manual = True            (the treatment was set by hand)
    creditable_percentage = 0.00    (only ever zeroed for gst_free /
                                     input_taxed / out_of_scope / not_registered)
    gst_treatment = 'gst'           (an off-choices value written *only* by
                                     core.views.review_bulk_edit_transactions
                                     from the account's tax_code)

So the sequence was: the accountant marked them GST-free, then assigned them to
account 630 (whose chart-of-accounts tax_code is GST), and the bulk-assign
endpoint overwrote both the treatment and the tax type from the account default.

core.views.review_bulk_edit_transactions no longer does this — a manually-set
treatment now wins over the account default. This script repairs the records
that were already overwritten.

It restores exactly what was allocated (gst_free -> "GST Free Income") and does
not attempt any further judgement.

NOTE for review after running: two of these are not income at all —
$70,000.00 "Transfer from xx3378 CommBank app To pay ATO" and $13,042.41
"Fast Transfer From UNYOKED HEALTH PTY LTD". Restoring them as GST-free leaves
them in G1 (total sales) with no GST, which is better than taxable but still
overstates turnover; they most likely belong at "BAS Excluded". There is also a
$950.00 line, "Page8of10) Statement10 ...", which looks like a statement page
header parsed as a transaction by the OCR.

Usage
-----
    python3 manage.py shell < data_fixes/fix_veronica_restore_gst_free_allocation.py
    APPLY=1 python3 manage.py shell < data_fixes/fix_veronica_restore_gst_free_allocation.py
"""
import json
import os
from decimal import Decimal

from django.db import transaction as db_transaction

from core.models import Entity
from review.models import PendingTransaction, canonical_tax_type

APPLY = os.environ.get("APPLY") == "1"
SNAPSHOT = "data_fixes/veronica_restore_gst_free_snapshot.json"

entities = list(Entity.objects.filter(entity_name="Veronica Cerratti Pty Ltd"))
assert entities, "entity not found"

candidates = []
for txn in PendingTransaction.objects.filter(
    job__entity__in=entities, is_confirmed=True, amount__gt=0
):
    raw = txn.confirmed_tax_type or txn.ai_suggested_tax_type or ""
    if canonical_tax_type(raw, is_income=True) != "GST on Income":
        continue
    # The fingerprint of an overwritten manual GST-free decision.
    if not txn.is_gst_manual:
        continue
    # NB: Decimal("0.00") is falsy, so `x or default` would silently skip every
    # row this is meant to match.
    if txn.creditable_percentage is None or txn.creditable_percentage != 0:
        continue
    candidates.append(txn)

total = sum((abs(t.amount) for t in candidates), Decimal("0"))
gst_backed_out = sum(
    (abs(t.confirmed_gst_amount if t.confirmed_gst_amount is not None else t.gst_amount)
     for t in candidates), Decimal("0")
)
print(f"{len(candidates)} transactions, {total} gross, {gst_backed_out} of GST to reverse")
for t in sorted(candidates, key=lambda x: -abs(x.amount)):
    print(f"   {t.date:<12} {abs(t.amount):>10} gst={t.confirmed_gst_amount:>9}"
          f"  {(t.description or '')[:56]}")

if not APPLY:
    print("\nDRY RUN — nothing written. Re-run with APPLY=1 to apply.")
else:
    snapshot = [
        {
            "id": str(t.id),
            "date": t.date,
            "amount": str(t.amount),
            "description": t.description,
            "confirmed_tax_type": t.confirmed_tax_type,
            "ai_suggested_tax_type": t.ai_suggested_tax_type,
            "gst_treatment": t.gst_treatment,
            "is_gst_manual": t.is_gst_manual,
            "creditable_percentage": str(t.creditable_percentage),
            "confirmed_gst_amount": str(t.confirmed_gst_amount),
            "gst_amount": str(t.gst_amount),
            "net_amount": str(t.net_amount),
        }
        for t in candidates
    ]
    with open(SNAPSHOT, "w") as fh:
        json.dump(snapshot, fh, indent=1)
    print(f"\nSnapshot written to {SNAPSHOT}")

    with db_transaction.atomic():
        for t in candidates:
            t.confirmed_tax_type = "GST Free Income"
            t.gst_treatment = "gst_free"          # was the off-choices 'gst'
            t.confirmed_gst_amount = Decimal("0.00")
            t.gst_amount = Decimal("0.00")
            t.net_amount = abs(t.amount)          # GST-free: net == gross
            t.save(update_fields=[
                "confirmed_tax_type", "gst_treatment", "confirmed_gst_amount",
                "gst_amount", "net_amount",
            ])
    print(f"APPLIED to {len(candidates)} transactions.")
    print("NOTE: the trial balance still holds the GST that was posted against")
    print("      these transactions — see the 3380 control-account drift.")
