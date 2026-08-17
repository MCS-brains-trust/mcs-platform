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
entities_examined = 0
entities_skipped = 0
txns_classified = 0
txns_unparseable = 0
txns_in_skipped_entities = 0

# Count transactions this probe cannot see because their job.entity is NULL.
txns_no_entity = PendingTransaction.objects.filter(
    is_confirmed=True, posted_to_tb=True, job__entity__isnull=True
).count()

# Note on the denominator: resolve_fy_for_txn currently has a fallback that
# returns the most recent POSTABLE year when no postable year covers a
# transaction's date. That fallback cannot fire on transactions whose job has
# no entity, because it looks up entity.entity_financial_years(). In this book,
# all 496 confirmed+posted transactions sit in jobs with job.entity=NULL; the
# 3 entities with postable years hold none. So resolve_fy_for_txn already
# returns None for every transaction (entity lookup returns None, then the
# function returns None immediately), and the strict rule changes nothing about
# existing postings. The gate is cleared by that argument, recorded in the
# ledger. This probe measures whether any transaction has ALREADY posted into
# a wrong year, which cannot happen if all transactions are already outside the
# fallback's reach. The per-entity loop below examines only transactions
# attached to entities, so it misses all 496 NULL-entity transactions — which
# is not a gap this probe should close (they are not at risk).

for entity in Entity.objects.all().order_by("entity_name"):
    postable = entity_financial_years(entity)
    if not postable:
        entities_skipped += 1
        skipped_txns = PendingTransaction.objects.filter(
            job__entity=entity, is_confirmed=True, posted_to_tb=True,
        ).count()
        txns_in_skipped_entities += skipped_txns
        continue
    entities_examined += 1
    all_years = list(FinancialYear.objects.filter(entity=entity))
    posted = PendingTransaction.objects.filter(
        job__entity=entity, is_confirmed=True, posted_to_tb=True,
    ).select_related("job")

    for txn in posted:
        txn_date = parse_txn_date(txn.date)
        if not txn_date:
            unparseable.append((entity, txn))
            txns_unparseable += 1
            continue
        txns_classified += 1
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

print(f"\nENTITIES")
print(f"  examined (have postable year): {entities_examined}")
print(f"  skipped (no postable year): {entities_skipped}, holding {txns_in_skipped_entities} confirmed+posted transactions")

print(f"\nTRANSACTIONS")
print(f"  in jobs with no entity (invisible to this probe): {txns_no_entity}")
print(f"  from examined entities, classified (date parsed): {txns_classified}")
print(f"  from examined entities, unparseable (date not parsed): {txns_unparseable}")

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
if txns_classified > 0 and total == 0:
    msg = f"ZERO wrong-year postings out of {txns_classified} classified"
    if txns_unparseable > 0:
        msg += f" ({txns_unparseable} unparseable, not classified)"
    msg += ". Safe to proceed with the strict rule."
    print(msg)
elif txns_classified == 0:
    msg = f"VACUOUS: 0 transactions classified ({txns_unparseable} unparseable, "
    msg += f"{txns_in_skipped_entities} in non-postable entities, {txns_no_entity} invisible "
    msg += "to per-entity loop). This probe proves NOTHING about wrong-year postings. "
    msg += "See the ledger ruling."
    print(msg)
else:
    print(f"{total} wrong-year posting(s). STOP — do not change the rule.")
    print("The rebuild would zero the trial-balance lines these created.")
print("Nothing was written.")
print(RULE)
