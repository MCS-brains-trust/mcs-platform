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


def resolve_bas_period_for_txn(txn):
    """Return the BASPeriod covering this transaction's date, or None.

    Periods are created lazily, so a transaction may fall in a range with no
    row. That is not a case to handle: no row means no lodgement, so there is
    nothing to flag.

    Note the deliberate difference from resolve_fy_for_txn: an unparseable date
    returns None here rather than falling back to the most recent year. The
    fallback exists so the rebuild reproduces what posting did; flagging a
    lodged period is an audit claim about a specific date, and a guess is worse
    than nothing.

    Filters on period_type, because a year really does hold both. Veronica
    Cerratti's live FY2026 carries 16 rows — 12 monthly and 4 quarterly, covering
    the same dates — while her bas_frequency is quarterly. An earlier version of
    this function left period_type out, on the assumption that periods are only
    ever created from the entity's own frequency and a year would therefore hold
    one type. Production says otherwise.

    The entity's bas_frequency is the only defensible choice: it is what
    bas_dashboard renders and what bas_lodge_period writes to, so anything else
    sets the amended flag on a row no screen will ever show — the flag is set,
    the badge never appears, and the feature silently does nothing.

    Note how the unfiltered version failed, because it is not symmetric.
    Meta.ordering is ["period_number"], and for any date the quarterly number is
    always <= the monthly one (Q = ceil(M/3)). So .first() picked the quarterly
    row almost always: accidentally right for a quarterly entity, always wrong
    for a monthly one. July was the exception either way, being period_number 1
    as both Q1 and Jul, with the tie broken by nothing at all.
    """
    from core.models import BASPeriod

    txn_date = parse_txn_date(txn.date)
    if not txn_date:
        return None
    fy = resolve_fy_for_txn(txn)
    if not fy:
        return None
    entity = txn.job.entity if txn.job else None
    period_type = getattr(entity, "bas_frequency", "quarterly") or "quarterly"
    return BASPeriod.objects.filter(
        financial_year=fy, period_type=period_type,
        period_start__lte=txn_date, period_end__gte=txn_date,
    ).first()


def flag_period_amended(txn, user=None):
    """Mark the transaction's BAS period as amended, if it is lodged.

    A correction inside a lodged period is allowed — the BAS detail tabs offer
    that workflow today. The trial balance rebuilds, the lodged snapshot stays
    frozen, and this flag makes the resulting divergence visible instead of
    silent. Returns the period it flagged, or None.
    """
    from django.utils import timezone

    period = resolve_bas_period_for_txn(txn)
    if period is None or period.status != "lodged":
        return None
    period.amended_since_lodgement = True
    period.amended_at = timezone.now()
    period.amended_by = user if (user and user.is_authenticated) else None
    period.save(update_fields=[
        "amended_since_lodgement", "amended_at", "amended_by",
    ])
    return period
