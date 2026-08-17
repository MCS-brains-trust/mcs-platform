# probe_wrong_year_postings.py
# READ-ONLY probe — has any posted transaction landed in a year its date does
# not cover?
#
#   cd <worktree>
#   python3 manage.py shell < probe_wrong_year_postings.py
#
# Pure read + print. No writes of any kind.
#
# WHY THIS GATES THE CHANGE. resolve_fy_for_txn currently falls back to the most
# recent POSTABLE year when no postable year covers a transaction's date, so such
# a transaction posted somewhere its date has nothing to do with. After the
# change it resolves to None, which means the aggregation excludes it — and the
# rebuild would then zero the trial-balance lines it created. That is data loss
# on historical client ledgers, so the count has to be zero before proceeding.
#
# Two ways in, reported separately because they need different decisions:
#   NON_POSTABLE  the date IS covered by one of the entity's years, but that year
#                 is finalised or reopened, so it is not a posting target
#   NO_YEAR       no year of the entity covers the date at all (a statement
#                 running past the last year that exists)

from core.models import Entity, FinancialYear
from core.txn_periods import entity_financial_years, parse_txn_date
from review.models import PendingTransaction

RULE = "=" * 78
non_postable, no_year, unparseable = [], [], []

for entity in Entity.objects.all().order_by("entity_name"):
    postable = entity_financial_years(entity)
    if not postable:
        continue
    all_years = list(FinancialYear.objects.filter(entity=entity))
    posted = PendingTransaction.objects.filter(
        job__entity=entity, is_confirmed=True, posted_to_tb=True,
    ).select_related("job")

    for txn in posted:
        txn_date = parse_txn_date(txn.date)
        if not txn_date:
            unparseable.append((entity, txn))
            continue
        if any(fy.start_date <= txn_date <= fy.end_date for fy in postable):
            continue  # resolves correctly today and after the change
        covering = [fy for fy in all_years if fy.start_date <= txn_date <= fy.end_date]
        landed = max(postable, key=lambda f: f.end_date)
        if covering:
            non_postable.append((entity, txn, covering[0], landed))
        else:
            no_year.append((entity, txn, landed))

print(RULE)
print("WRONG-YEAR POSTING PROBE — read-only")
print(RULE)

print(f"\nNON_POSTABLE — date falls in a year that cannot receive postings: {len(non_postable)}")
for entity, txn, covering, landed in non_postable[:40]:
    print(f"  {entity.entity_name} | {txn.date} {txn.amount} {txn.confirmed_code} | "
          f"date is in {covering.year_label} (status {covering.status!r}) | "
          f"posted to {landed.year_label}")
if len(non_postable) > 40:
    print(f"  … and {len(non_postable) - 40} more")

print(f"\nNO_YEAR — no financial year covers the date: {len(no_year)}")
for entity, txn, landed in no_year[:40]:
    print(f"  {entity.entity_name} | {txn.date} {txn.amount} {txn.confirmed_code} | "
          f"posted to {landed.year_label}")
if len(no_year) > 40:
    print(f"  … and {len(no_year) - 40} more")

print(f"\nUNPARSEABLE DATE (informational — behaviour unchanged by this work): {len(unparseable)}")

print("\n" + RULE)
total = len(non_postable) + len(no_year)
if total == 0:
    print("ZERO wrong-year postings. Safe to proceed with the strict rule.")
else:
    print(f"{total} wrong-year posting(s). STOP — do not change the rule.")
    print("The rebuild would zero the trial-balance lines these created.")
print("Nothing was written.")
print(RULE)
