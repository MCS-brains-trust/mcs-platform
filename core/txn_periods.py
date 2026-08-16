"""Resolve a PendingTransaction's free-text date onto the periods it belongs to.

PendingTransaction.date is a CharField holding whatever the statement parser
produced, so every consumer needing a real date has to parse it. Four consumers
need it — the posting path, the trial-balance rebuild, the bank-contra
recalculation and the amended-period flag — and three of them used to answer
"which financial year is this?" differently. A statement spanning a year end
therefore posted to one year and had its bank contra counted into another.

One rule, one implementation. Everything asks here.
"""
from datetime import datetime

# The formats the statement parsers are known to emit. Taken verbatim from the
# posting path so the rebuild parses exactly what posting parsed.
DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d %b %Y")

# A financial year is only a posting target while it is open for work. Matches
# the filter the posting path has always used.
POSTABLE_FY_STATUSES = ("draft", "in_review", "finished")


def parse_txn_date(raw):
    """Parse a PendingTransaction.date string to a date, or None if unparseable."""
    if not raw:
        return None
    try:
        raw = raw.strip()
    except AttributeError:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def entity_financial_years(entity):
    """Every financial year a transaction for this entity could post to."""
    from core.models import FinancialYear

    return list(
        FinancialYear.objects.filter(
            entity=entity, status__in=POSTABLE_FY_STATUSES
        )
    )


def resolve_fy_for_txn(txn, fys=None):
    """Return the FinancialYear this transaction posts to, or None.

    The year whose date range covers the transaction's date. When the date is
    unparseable, or falls outside every year, fall back to the most recent year
    — the fallback the posting path has always used.

    The rebuild must reproduce this exactly, fallback included. Filtering on the
    date range instead would drop every unparseable-date transaction out of the
    year posting put it in, and the rebuild would then zero lines it had
    legitimately created — turning the rebuild into the data loss it exists to
    prevent.

    Pass `fys` from entity_financial_years() when resolving many transactions
    for one entity, to keep the batch to a single query.
    """
    entity = txn.job.entity if txn.job else None
    if not entity:
        return None
    if fys is None:
        fys = entity_financial_years(entity)
    if not fys:
        return None

    txn_date = parse_txn_date(txn.date)
    if txn_date:
        for fy in fys:
            if fy.start_date <= txn_date <= fy.end_date:
                return fy

    return max(fys, key=lambda f: f.end_date)
