"""Resolve a PendingTransaction's free-text date onto the periods it belongs to.

PendingTransaction.date is a CharField holding whatever the statement parser
produced, so every consumer needing a real date has to parse it. Four consumers
need it — the posting path, the trial-balance rebuild, the bank-contra
recalculation and the amended-period flag — and three of them used to answer
"which financial year is this?" differently. A statement spanning a year end
therefore posted to one year and had its bank contra counted into another.

One rule, one implementation. Everything asks here.

The rule has an outcome it did not originally have: **no year, do not post**. A
parseable date that no postable year covers resolves to None rather than falling
back to the most recent open year, because posting it there put it in a year it
had nothing to do with — a statement running to 31 July 2026 overstated FY2026.
`None` from resolve_fy_for_txn is therefore deliberate and means "this does not
post anywhere yet"; `unpostable_reason` turns it into a sentence for the user,
and the transaction posts itself once the year it belongs to exists.

The fallback survives for one case only: a date that cannot be parsed at all,
where there is nothing to reason from. Two tests pin that deliberately.
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

    Three outcomes: the postable year whose date range covers the
    transaction's date; `None` when the date is known but no postable year
    covers it — posting it anywhere would put it in a year it has nothing to
    do with; and, only when the date itself cannot be parsed, the most recent
    year — the fallback the posting path has always used, kept because there
    is nothing to reason from.

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
    if not txn_date:
        # Unparseable date: there is nothing to reason from, so keep the
        # historical fallback. Deliberately NOT changed — see the spec's
        # decision 3. Making these unpostable would strand transactions whose
        # date may not be editable, and unreadable-date rows are the ones most
        # likely to be already posted through this fallback.
        return max(fys, key=lambda f: f.end_date)

    for fy in fys:
        if fy.start_date <= txn_date <= fy.end_date:
            return fy

    # The date is known and no POSTABLE year covers it. Posting it anywhere
    # would put it in a year it has nothing to do with — which is how a
    # statement running to 31 July 2026 overstated FY2026. Returning None means
    # "do not post": _post_confirmed_txn_to_tb returns False, and every
    # aggregation caller excludes it, so the rebuild can never zero a line for a
    # transaction it also refuses to post.
    return None


def unpostable_reason(txn):
    """Why this transaction cannot post, or None if it can.

    Derived rather than stored: is_confirmed=True with posted_to_tb=False
    already records the state, and the explanation follows from the date plus
    the entity's years. Called only when posting was skipped, so the ordinary
    path pays for no extra query.

    Looks at ALL of the entity's years, not just the postable ones, because that
    is what distinguishes the two cases — a year that exists but cannot receive
    postings, versus no year at all. Only the second is fixed by creating a year,
    so the messages must not be interchangeable.
    """
    from core.models import FinancialYear

    if resolve_fy_for_txn(txn) is not None:
        return None

    # The entity is checked before the date, because a transaction with no
    # entity has no years to reason about and the date tells us nothing more.
    # Every confirmed+posted transaction in the book today sits in a job whose
    # entity is NULL, so this is the live case, not a theoretical one.
    entity = txn.job.entity if txn.job else None
    if entity is None:
        return "This transaction is not attached to an entity."

    txn_date = parse_txn_date(txn.date)
    if not txn_date:
        # An unparseable date still posts, via the fallback, so it is not
        # unpostable and resolve_fy_for_txn above would have returned a year.
        # Reaching here means the entity has no postable year at all.
        return "This entity has no financial year open for posting."

    shown = txn_date.strftime("%d %b %Y")
    covering = FinancialYear.objects.filter(
        entity=entity, start_date__lte=txn_date, end_date__gte=txn_date,
    ).first()
    if covering:
        return (
            f"{covering.year_label} covers {shown} but its status is "
            f"'{covering.status}', so it cannot receive postings."
        )
    return (
        f"No financial year covers {shown}. Create that year to post this "
        f"transaction — it will post itself once the year exists."
    )


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
