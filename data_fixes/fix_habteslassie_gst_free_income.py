"""
Data repair — Daniel Habteslassie FY2026: restore GST-free treatment on
misclassified medical income.

Background
----------
42 income transactions carry confirmed_tax_type='GST' (a chart-of-accounts tax
code, not a TAX_TYPE_CHOICES value) while simultaneously carrying
gst_treatment='gst_free' and confirmed_gst_amount=0.00. They are health-fund and
Medicare receipts — Medicare, BUPA, HCF, Medibank, GMHBA, Australian Unity,
Teachers Health, Defence Health, Monash Health and Tyro patient settlements —
i.e. the same payers as the 311 receipts already treated as GST-free. Medical
services are GST-free under GST Act s38-7.

The accountant marked them GST-free; an unvalidated write path later overwrote
confirmed_tax_type with the account's tax code and left the treatment and the
zero GST untouched. The BAS engine then read them as taxable.

Only 3 income transactions on this file are genuinely taxable (sleep-clinic
reporting fees, $968.00 with $88.00 of GST actually booked); they are untouched
because they fail the "GST is zero" test below.

Effect: 1A falls from $3,873.00 to $88.00; net GST goes from $2,694.97 refundable
to $6,479.97 refundable.

The trial balance needs no adjustment: these transactions already posted their
full gross to income with nothing to the GST control account, which is what
GST-free treatment requires.

Usage
-----
    python3 manage.py shell < data_fixes/fix_habteslassie_gst_free_income.py          # dry run
    APPLY=1 python3 manage.py shell < data_fixes/fix_habteslassie_gst_free_income.py  # apply

A JSON snapshot of the prior values is written before any change. To reverse:

    SNAPSHOT=data_fixes/habteslassie_gst_free_income_snapshot.json APPLY=1 \
        python3 manage.py shell < data_fixes/revert_from_snapshot.py
"""
import json
import os
from decimal import Decimal

from django.db import transaction as db_transaction

from core.models import Entity
from review.models import PendingTransaction, canonical_tax_type

APPLY = os.environ.get("APPLY") == "1"
SNAPSHOT = "data_fixes/habteslassie_gst_free_income_snapshot.json"

entities = list(Entity.objects.filter(entity_name="Daniel Habteslassie"))
assert entities, "entity not found"

candidates = []
for txn in PendingTransaction.objects.filter(
    job__entity__in=entities, is_confirmed=True, amount__gt=0
):
    raw = txn.confirmed_tax_type or txn.ai_suggested_tax_type or ""
    if canonical_tax_type(raw, is_income=True) != "GST on Income":
        continue
    stored = txn.confirmed_gst_amount
    if stored is None:
        stored = txn.gst_amount
    # Genuinely taxable income has GST booked against it. Zero GST plus a
    # gst_free treatment is the signature of the overwrite.
    if abs(stored or Decimal("0")) != 0:
        continue
    if txn.gst_treatment != "gst_free":
        continue
    candidates.append(txn)

total = sum((abs(t.amount) for t in candidates), Decimal("0"))
print(f"{len(candidates)} transactions, {total} gross")
for t in sorted(candidates, key=lambda x: x.date)[:100]:
    print(f"   {t.date:<12} {abs(t.amount):>10}  {t.confirmed_tax_type!r:<8}"
          f" -> 'GST Free Income'   {(t.description or '')[:52]}")

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
            # gst_treatment is already 'gst_free'; GST is already 0.00 and
            # net_amount already equals the gross, which is correct for
            # GST-free income. Only the tax-type label is wrong.
            t.save(update_fields=["confirmed_tax_type"])
    print(f"APPLIED to {len(candidates)} transactions.")
