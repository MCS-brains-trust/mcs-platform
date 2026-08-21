"""
Geometry-based bank statement parser using pdfplumber word coordinates.

Auto-detects debit/credit column positions by clustering the right-edge (x1)
of bare-amount tokens across the statement.  Validates every statement against
its own running-balance reconciliation before returning.

Return shape matches extract_transactions_from_pdf_direct:
    {opening_balance, closing_balance, account_name, bsb, account_number,
     period_start, period_end, transactions: [{date, description, amount}]}

amounts are signed: credit > 0, debit < 0.
"""
import io
import logging
import re
from collections import defaultdict

import pdfplumber

logger = logging.getLogger(__name__)


class StatementParseError(Exception):
    """Raised when a statement cannot be parsed or fails reconciliation."""


# Bare monetary amount — no currency symbol, no CR/DR: "1,234.56"
MOVE_RE = re.compile(r'^\d{1,3}(,\d{3})*\.\d{2}$')
# The same, tolerating a leading '$'. Older CBA statements print the symbol on
# some columns and not others, and while it was not tolerated every $-prefixed
# figure was invisible: on one real statement the whole credit column carried a
# $, so column detection saw only debits, found fewer than two columns, and
# rejected a statement whose 108 transactions were perfectly legible.
#
# Deliberately NOT used by the NAB path below. Every bank here prints some
# $-prefixed figures (34 of them on each NAB statement), so widening the shared
# pattern would change which figures NAB reads as column entries — a change
# with no evidence behind it and no place in CBA work.
CBA_MOVE_RE = re.compile(r'^\$?\d{1,3}(,\d{3})*\.\d{2}$')
# Running-balance token: "1,234.56 CR" or "1,234.56 DR"
BAL_RE = re.compile(r'(\d{1,3}(?:,\d{3})*\.\d{2})\s*(CR|DR)')
# Glued CBA date token: "31Oct" or "31Oct2025"
DATE_RE = re.compile(r'^(\d{1,2})(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)', re.IGNORECASE)
# Per-page furniture patterns — matched against space-stripped flat row text
_FURNITURE_PAGE_RE = re.compile(r'Page\d+of\d+')
_FURNITURE_BATCH_RE = re.compile(r'^\d{5}\.\d{5}')
# Column header, matched WITHOUT its leading 'Date'. The header's own cells are
# split across two word-rows on most pages -- the 'Date' cell is typeset a
# fraction of a point off the others, so round(top) puts it in a row of its
# own -- and requiring the glued 'Date...' prefix missed the header on 10 pages
# out of 12 on a real statement.
_FURNITURE_COL_HEADER = 'TransactionDebitCreditBalance'
_FURNITURE_ACCOUNT_KW = 'AccountNumber'
# 4-digit year in the 2000s
YEAR_RE = re.compile(r'(20\d{2})')

# The date as two adjacent words -- '01' then 'Jul' -- which is how the older
# CBA layout prints it. DATE_RE only matches the glued form ('01Jul'), so on
# those statements no row ever started a transaction and every transaction was
# built with date=None: 108 of 108 on one real statement. A dateless
# transaction cannot be filtered to a financial year or checked for statement
# order, so this is not cosmetic.
DAY_RE = re.compile(r'^(\d{1,2})$')
MONTH_WORD_RE = re.compile(
    r'^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)$', re.IGNORECASE)

MONTH_MAP = {
    'Jan': '01', 'Feb': '02', 'Mar': '03', 'Apr': '04',
    'May': '05', 'Jun': '06', 'Jul': '07', 'Aug': '08',
    'Sep': '09', 'Oct': '10', 'Nov': '11', 'Dec': '12',
}

# Bank profile — extensibility hook for future banks.
# money_model 'two_col': two bare-amount columns (debit=left, credit=right).
# money_model 'signed_amount': single Amount column with -/DR prefix (out of scope here).
CBA_PROFILE = dict(
    bank_key='cba',
    date_re=DATE_RE,
    money_model='two_col',
    opening_kw='OPENINGBALANCE',
    closing_kw='CLOSINGBALANCE',
)


def _f(s):
    """'1,853.60' or '$1,853.60' -> 1853.60"""
    return float(s.replace(',', '').replace('$', ''))


def _truncate(text, max_len):
    if len(text) <= max_len:
        return text
    cut = text[:max_len].rsplit(' ', 1)[0]
    return cut or text[:max_len]


def _signed_balance(text):
    """Extract signed balance from text containing 'amount CR/DR'. CR=positive, DR=negative."""
    m = BAL_RE.search(text)
    if not m:
        return None
    return _f(m.group(1)) * (1 if m.group(2) == 'CR' else -1)


def _rows(pdf):
    """
    Return word-rows (sorted lists) top-to-bottom across all pages, up to and
    including the LAST CLOSING BALANCE marker.

    Multi-period (e.g. multi-month) CBA statements contain several
    OPENINGBALANCE/CLOSINGBALANCE sub-periods. Stopping at the *first*
    CLOSINGBALANCE would silently drop every later month's transactions (and
    month 1 reconciles internally, so the truncation goes unnoticed). We
    accumulate rows across all sub-periods and trim only the trailing furniture
    after the final CLOSINGBALANCE.

    NOTE: this test is deliberately CASE SENSITIVE and must stay that way.
    CBA's NetBank Transaction History export states its closing balance in the
    HEADER, above the table, as "Closing balance" -- matching it case
    insensitively would trim that document to its first five rows and lose all
    35 transactions.
    """
    out = []
    last_closing_idx = None
    for page in pdf.pages:
        lines = defaultdict(list)
        for w in page.extract_words():
            lines[round(w['top'])].append(w)
        for top in sorted(lines):
            row = sorted(lines[top], key=lambda w: w['x0'])
            out.append(row)
            if 'CLOSINGBALANCE' in ''.join(w['text'] for w in row):
                last_closing_idx = len(out) - 1
    if last_closing_idx is not None:
        return out[:last_closing_idx + 1]
    return out


def _header_money_columns(rows):
    """Right edges of the debit and credit columns, read off the table header.

    Preferred over clustering the figures, because population cannot tell a
    sparse real column from noise. One real CBA statement carried 95 debits and
    6 credits; the population floor in ``_money_columns`` discarded the credit
    column, leaving a single cluster and no way to tell debits from credits.
    The header states both positions outright and does not care how many
    entries each column happens to have.

    Only fires on statements whose header words are separately extractable. On
    the modern CBA layout the whole header collapses into one glued token
    ('DateTransactionDebitCreditBalance'), so those statements fall through to
    clustering, which already handles them.

    Returns (debit_x1, credit_x1) or None.
    """
    for row in rows:
        by_text = {}
        for word in row:
            by_text.setdefault(word['text'].strip().rstrip(':'), word)
        for debit_label, credit_label in (('Debit', 'Credit'),
                                          ('Debits', 'Credits')):
            if {debit_label, credit_label} <= set(by_text):
                return (by_text[debit_label]['x1'],
                        by_text[credit_label]['x1'])
    return None


def _figures_in_columns(rows, debit_x, credit_x, tolerance=12.0):
    """How many figures actually land in the columns a header claims.

    A header label's x-position is a hypothesis about where its column is, not
    a fact. A statement can print the header as a run of running text whose
    labels sit nowhere above the figures, and trusting it then yields column
    positions no figure matches -- every movement missed and the statement
    rejected for having no movements at all. Verify the hypothesis against the
    figures before acting on it.
    """
    hits = 0
    for row in rows:
        for word in row:
            if not CBA_MOVE_RE.match(word['text']):
                continue
            if (abs(word['x1'] - debit_x) <= tolerance
                    or abs(word['x1'] - credit_x) <= tolerance):
                hits += 1
    return hits


def _money_columns(rows, min_count=None, gap=12.0):
    """
    Auto-detect debit/credit column x1 centres by clustering bare-amount right-edges.
    Returns (debit_x, credit_x) or None if two distinct clusters cannot be found.

    ``min_count`` is the minimum cluster population to count as a money column.
    When not supplied it scales with the number of amount tokens (floor 2) so
    low-activity statements (only a handful of transactions) are not rejected,
    while a fixed high threshold no longer over-filters them.
    """
    xs = sorted(
        w['x1'] for row in rows for w in row if CBA_MOVE_RE.match(w['text']))
    if len(xs) < 2:
        return None
    if min_count is None:
        min_count = max(2, len(xs) // 20)
    groups = [[xs[0]]]
    for x in xs[1:]:
        if x - groups[-1][-1] <= gap:
            groups[-1].append(x)
        else:
            groups.append([x])
    cols = [(sum(g) / len(g), len(g)) for g in groups if len(g) >= min_count]
    if len(cols) < 2:
        return None
    money = sorted(cols, key=lambda c: -c[1])[:2]   # two most-populous clusters
    money.sort()                                      # left=debit, right=credit
    return money[0][0], money[1][0]


# A single stray token may sit to the left of the date, in the margin: a
# codeline fragment ('5.2.62173.78031'), a barcode tail ('3R852ZZ') or a
# continuation asterisk. Those rows do start a transaction, and requiring the
# date at position 0 meant they silently produced one with no date at all --
# which confirm_import then drops, breaking reconciliation and getting the
# whole statement refused. One leading token is tolerated; more than one would
# start guessing.
_MAX_DATE_OFFSET = 1


def _row_date_parts(row):
    """(day, month, start, end) when a row begins with a date, else None.

    ``start`` is the index of the first date token and ``end`` the index after
    the last, so a caller can tell the two forms apart: the glued form
    ('01May...') occupies one token and may carry description text behind the
    date, while the spaced form ('01', 'Jul') occupies two and carries none.

    The date is looked for at the start of the row or immediately after one
    stray margin token.
    """
    if not row:
        return None
    for offset in range(min(_MAX_DATE_OFFSET, len(row) - 1) + 1):
        text = row[offset]['text']
        glued = DATE_RE.match(text)
        if glued:
            return (glued.group(1),
                    glued.group(2).capitalize()[:3],
                    offset, offset + 1)
        if (DAY_RE.match(text) and len(row) > offset + 1
                and MONTH_WORD_RE.match(row[offset + 1]['text'])):
            return (text,
                    row[offset + 1]['text'].capitalize()[:3],
                    offset, offset + 2)
    return None


def _text_only(row):
    """
    Description text from a row: drop pure-amount, CR/DR, and date tokens.
    Date tokens (e.g. '31Oct') are excluded so they don't contaminate descriptions.
    """
    keep = []
    # Drop whatever sits to the LEFT of the date -- the stray margin token that
    # otherwise prefixed the description ('5.2.62173.78031 27 Jul JOHN ...').
    #
    # Where the date is spaced ('01', 'Jul') its own tokens go too, since they
    # carry nothing else. Where it is glued the token is left in place for the
    # loop below, whose DATE_RE branch keeps the description behind the date --
    # '01MayDirectCredit...' must still yield 'DirectCredit...'.
    parts = _row_date_parts(row)
    if parts:
        _, _, date_start, date_end = parts
        body = row[date_end if date_end - date_start == 2 else date_start:]
    else:
        body = row
    for w in body:
        t = w['text']
        if CBA_MOVE_RE.match(t):
            continue
        # A lone '$' is a column decoration, not description text.
        if t == '$':
            continue
        if t in ('CR', 'DR'):
            continue
        m = DATE_RE.match(t)
        if m:
            # The date cell is often set tight enough to glue onto the
            # description, so the row arrives as one word
            # ('01MayDirectCredit002221MCAREBENEFITS'). Dropping the whole
            # token as "a date" threw away the transaction's primary
            # description line -- 152 of 240 rows on one real statement --
            # leaving only its continuation line ('MCBBS706460616AW') to be
            # coded from. Keep whatever follows the date token.
            rest = t[m.end():]
            if rest:
                keep.append(rest)
            continue
        keep.append(t)
    return ' '.join(keep)


def _orphaned_anchor_amount(rows, idx):
    """Recover an anchor amount that row grouping split onto its own row.

    ``_rows`` groups words by ``round(top)``, so a figure typeset a fraction of
    a point off its own label lands in a neighbouring row. On a real CBA
    statement the closing figure sat 0.675pt above 'CLOSINGBALANCE' and rounded
    away from it (155.199 -> 155 against 155.874 -> 156), leaving the keyword
    row with no amount at all. That rejected a statement whose 240
    transactions were otherwise extracted perfectly, and dropped the upload
    into the Vision OCR fallback.

    Only a neighbouring row carrying nothing but one amount (and optionally
    CR/DR) is accepted, so a transaction's own figure can never be mistaken for
    the anchor. The sign is read from the label row first, because the CR/DR
    marker normally stays with the keyword while only the figure drifts.
    ``_reconcile`` still has to agree with whatever is recovered here.
    """
    label_texts = [w['text'] for w in rows[idx]]
    for j in (idx - 1, idx + 1):
        if not 0 <= j < len(rows):
            continue
        texts = [w['text'] for w in rows[j]]
        if not texts:
            continue
        if not all(CBA_MOVE_RE.match(t) or t in ('CR', 'DR') for t in texts):
            continue
        amounts = [t for t in texts if CBA_MOVE_RE.match(t)]
        if len(amounts) != 1:
            continue
        negative = 'DR' in label_texts or 'DR' in texts
        return _f(amounts[0]) * (-1 if negative else 1)
    return None


def _is_furniture(flat):
    """Return True if the space-stripped row text is page furniture, not transaction data."""
    if _FURNITURE_PAGE_RE.search(flat):
        return True
    # The 'Date' column header cell, orphaned into a row of its own by the same
    # sub-point offset that splits the rest of the header.
    if flat == 'Date':
        return True
    if _FURNITURE_COL_HEADER in flat:
        return True
    # 'AccountNumber' never appears in a real transaction description
    if _FURNITURE_ACCOUNT_KW in flat:
        return True
    # Batch/codeline header: starts with 5digit.5digit (e.g. "15448.35330")
    if _FURNITURE_BATCH_RE.match(flat):
        return True
    return False


def _reconcile(txns, opening, closing, tolerance=0.01):
    """
    opening + sum(signed amounts) must equal closing.
    Raises StatementParseError with a full diagnostic on mismatch.
    """
    if opening is None or closing is None:
        raise StatementParseError("Missing opening/closing balance anchor")
    total = sum(t['amount'] for t in txns)
    derived = round(opening + total, 2)
    if abs(derived - closing) > tolerance:
        raise StatementParseError(
            f"Reconciliation failed: open {opening:.2f} + movements {total:.2f} "
            f"= {derived:.2f}, expected closing {closing:.2f} "
            f"(delta {derived - closing:+.2f})"
        )
    return True


def parse_cba_geometry(pdf_content):
    """
    Parse a CBA bank statement PDF using word-coordinate geometry.

    Raises StatementParseError if column detection, year extraction, or
    balance reconciliation fails — never returns partial/empty results silently.
    """
    with pdfplumber.open(io.BytesIO(pdf_content)) as pdf:
        rows = _rows(pdf)

    # Header first, data second: the header states the columns while the data
    # only implies them -- but only once the figures agree that the header's
    # labels really do sit above its columns.
    cols = _header_money_columns(rows)
    if cols and _figures_in_columns(rows, *cols) < 2:
        cols = None
    cols = cols or _money_columns(rows)
    if not cols:
        raise StatementParseError("Could not detect debit/credit columns")
    debit_x, credit_x = cols

    # Pass 1 — extract opening balance and statement year from the OPENING BALANCE row.
    # The year is embedded in that row (e.g. "31Oct 2025OPENINGBALANCE").
    opening = None
    base_year = None
    for i, row in enumerate(rows):
        flat = ''.join(w['text'] for w in row)
        if 'OPENINGBALANCE' in flat:
            joined = ' '.join(w['text'] for w in row)
            opening = _signed_balance(joined)
            if opening is None:
                opening = _orphaned_anchor_amount(rows, i)
            for w in row:
                m = YEAR_RE.search(w['text'])
                if m:
                    base_year = m.group(1)
                    break
            break

    if base_year is None:
        raise StatementParseError(
            "Could not extract statement year from OPENING BALANCE row — "
            "cannot assign ISO dates without a known base year"
        )
    if opening is None:
        raise StatementParseError("Could not parse opening balance amount from OPENING BALANCE row")

    # Pass 2 — walk rows and assemble transactions.
    txns = []
    desc = []
    date = None
    prev_month = None
    cur_year = base_year
    closing = None

    for i, row in enumerate(rows):
        flat = ''.join(w['text'] for w in row)
        joined = ' '.join(w['text'] for w in row)

        if 'OPENINGBALANCE' in flat:
            desc = []
            continue
        if 'CLOSINGBALANCE' in flat:
            closing = _signed_balance(joined)
            if closing is None:
                closing = _orphaned_anchor_amount(rows, i)
            desc = []
            continue

        # Skip per-page furniture: page numbers, column headers, account labels, batch codes.
        # Must come before date/amount/desc logic so furniture never starts a transaction.
        if _is_furniture(flat):
            # The column header also marks where a page's real rows begin, so
            # anything still buffered when we reach it is preamble or page
            # furniture that escaped the patterns above -- the bare
            # 'Statement11' title, a lone account number, the address block.
            # Left in place it is prefixed onto this page's first description,
            # which is how 'Your Statement 06269281523335 031 1301011...' ended
            # up as the description of a real transaction on an imported
            # statement. Enumerating every stray title is a losing game; the
            # header is the reliable boundary.
            if _FURNITURE_COL_HEADER in flat:
                desc = []
            continue

        # New transaction row starts when the first word is a date token
        # and we are not already mid-transaction (date is None).
        parts = _row_date_parts(row) if date is None else None
        if parts:
            day = parts[0].zfill(2)
            month_str = parts[1]
            del parts
            month_num = int(MONTH_MAP[month_str])
            # Dec→Jan year rollover (9a92915 logic)
            if prev_month is not None and prev_month == 12 and month_num == 1:
                cur_year = str(int(cur_year) + 1)
            prev_month = month_num
            date = f"{cur_year}-{MONTH_MAP[month_str]}-{day}"

        # Identify whether this row carries a movement amount and in which column.
        movement = None
        for w in row:
            if CBA_MOVE_RE.match(w['text']):
                if abs(w['x1'] - debit_x) <= 12.0:
                    movement = ('debit', _f(w['text']))
                elif abs(w['x1'] - credit_x) <= 12.0:
                    movement = ('credit', _f(w['text']))

        desc.append(_text_only(row))

        if movement:
            amount = movement[1] if movement[0] == 'credit' else -movement[1]
            txns.append({
                'date': date,
                'description': _truncate(' '.join(d for d in desc if d).strip(), 200),
                'amount': amount,
            })
            desc = []
            date = None

    _reconcile(txns, opening, closing)

    return {
        'opening_balance': opening,
        'closing_balance': closing,
        'account_name': '',
        'bsb': '',
        'account_number': '',
        'period_start': '',
        'period_end': '',
        'transactions': txns,
    }


# --------------------------------------------------------------------------
# Westpac
# --------------------------------------------------------------------------

# Westpac dates its rows "28/02/22".
# Debit, credit and balance figures, no currency symbol.
# How close a figure's right edge must be to a column's to belong to it. The
# figures right-align on the column, so this only absorbs sub-point drift.
WESTPAC_COLUMN_TOLERANCE = 4.0





def parse_westpac_statement_geometry(pdf_content):
    """Parse a Westpac statement. Kept as a name of its own because
    pdf_parsers routes on it; the work is done by the shared column-table
    engine below, which Westpac, ANZ and Bendigo all use.
    """
    return parse_column_table_statement(pdf_content, 'westpac')

# --------------------------------------------------------------------------
# Shared column-table engine: Westpac, ANZ, Bendigo
# --------------------------------------------------------------------------
#
# These three banks print the same shape -- a date, a description, two money
# columns (out and in), and a running balance -- and differ only in what they
# call the columns, how they write the date, and where they state their
# anchors. The per-line text parsers each failed on that shape in their own
# way: Westpac lost ~93% of rows because the figure sits on the row after the
# date, and ANZ dropped a 5,706.00 deposit because it marks an empty column
# with the literal word "blank" and that row simply had none. Reading
# coordinates makes both non-issues, so the engine is shared and only the
# differences are declared.

# Figures in these columns, optionally $-prefixed (ANZ prints "$14,400.93" on
# its totals row). Confined to this engine: the NAB path deliberately keeps the
# strict MOVE_RE, since widening a shared pattern broke it once already.
TABLE_MONEY_RE = re.compile(r'^-?\$?\d{1,3}(,\d{3})*\.\d{2}$')
TABLE_COLUMN_TOLERANCE = 4.0

_MONTH_ABBR = r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)'
_DDMMYY_RE = re.compile(r'^(\d{2})/(\d{2})/(\d{2})$')
_DD_RE = re.compile(r'^(\d{1,2})$')
_MON_RE = re.compile(r'^' + _MONTH_ABBR + r'$', re.IGNORECASE)
_YEAR_ONLY_RE = re.compile(r'^(20\d{2})$')
# "12Mar25", "1Jun23" -- Bendigo glues day, month and a 2-digit year.
_DDMONYY_RE = re.compile(r'^(\d{1,2})' + _MONTH_ABBR + r'(\d{2})$', re.IGNORECASE)
# "1Mar" with the year in the next token.
_DDMON_RE = re.compile(r'^(\d{1,2})' + _MONTH_ABBR + r'$', re.IGNORECASE)
# "15/06/2026" -- the NetBank export writes the century out.
_DDMMYYYY_RE = re.compile(r'^(\d{2})/(\d{2})/(\d{4})$')


def _date_ddmmyy(row, year_hint):
    """Westpac: '28/02/22' in the first token."""
    m = _DDMMYY_RE.match(row[0]['text'])
    if not m:
        return None
    day, month, year = m.groups()
    return f"20{year}-{month}-{day}"


def _date_dd_mon(row, year_hint):
    """ANZ: '08' 'NOV' as two tokens, the year stated on its own row earlier."""
    if len(row) < 2 or not year_hint:
        return None
    if not (_DD_RE.match(row[0]['text']) and _MON_RE.match(row[1]['text'])):
        return None
    month = MONTH_MAP[row[1]['text'].capitalize()[:3]]
    return f"{year_hint}-{month}-{row[0]['text'].zfill(2)}"


def _date_ddmonyy(row, year_hint):
    """Bendigo: '12Mar25' glued, or '1Mar' with '25' in the next token."""
    glued = _DDMONYY_RE.match(row[0]['text'])
    if glued:
        day, month, year = glued.groups()
        return (f"20{year}-{MONTH_MAP[month.capitalize()[:3]]}-{day.zfill(2)}")
    split = _DDMON_RE.match(row[0]['text'])
    if split and len(row) > 1 and re.match(r'^\d{2}$', row[1]['text']):
        day, month = split.groups()
        return (f"20{row[1]['text']}-{MONTH_MAP[month.capitalize()[:3]]}"
                f"-{day.zfill(2)}")
    return None


# "StatementPeriod" "20/05/2022to19/11/2022" -- Bank of Melbourne glues the two
# dates into one token, and its transaction rows carry no year at all.
_PERIOD_RE = re.compile(
    r'^(\d{2})/(\d{2})/(\d{4})to(\d{2})/(\d{2})/(\d{4})$')


def _year_only_hint(row):
    """ANZ prints the year on a row of its own ahead of the months it covers."""
    if row and _YEAR_ONLY_RE.match(row[0]['text']):
        return row[0]['text']
    return None


def _statement_period_hint(row):
    """The statement period, as ((y, m, d), (y, m, d)), or None.

    Bank of Melbourne dates its transactions "20MAY" with no year anywhere on
    the row, so the year has to come from the period this table belongs to --
    and a period can straddle a new year (20/11/2022 to 19/05/2023). Each
    period restates its own StatementPeriod line ahead of its table, which is
    what makes a multi-period bundle readable at all.
    """
    for word in row:
        m = _PERIOD_RE.match(word['text'])
        if m:
            d1, m1, y1, d2, m2, y2 = (int(g) for g in m.groups())
            return ((y1, m1, d1), (y2, m2, d2))
    return None


def _date_ddmmyyyy(row, hint):
    """CBA's NetBank Transaction History export: '15/06/2026', full year."""
    m = _DDMMYYYY_RE.match(row[0]['text'])
    if not m:
        return None
    day, month, year = m.groups()
    return f"{year}-{month}-{day}"


def _date_ddmon_in_period(row, hint):
    """Bank of Melbourne: '20MAY', with the year taken from the period.

    The year is whichever of the period's two years places the date inside the
    period. A statement running Nov to May holds both, and guessing the first
    would date January's rows twelve months early -- which reconciles
    perfectly, because reconciliation never looks at dates.
    """
    if not hint:
        return None
    match = _DDMON_RE.match(row[0]['text'])
    if not match:
        return None
    day, month = int(match.group(1)), int(MONTH_MAP[match.group(2).capitalize()[:3]])
    (start, end) = hint
    for year in (start[0], end[0]):
        if start <= (year, month, day) <= end:
            return f"{year:04d}-{month:02d}-{day:02d}"
    return None


def _row_money(row):
    """(word, value) for every figure in the row, in reading order."""
    out = []
    for word in row:
        if TABLE_MONEY_RE.match(word['text']):
            out.append((word, _f(word['text'])))
    return out


def _table_columns(rows, labels):
    """Right edges of the out, in and balance columns from the table header.

    A label may be followed by a unit token -- ANZ writes "Withdrawals ($)" --
    and the figures align with the unit, not the word, so the rightmost token of
    the label group is what anchors the column.
    """
    out_label, in_label, balance_label = labels
    for row in rows:
        by_text = {}
        for index, word in enumerate(row):
            by_text.setdefault(word['text'].strip().upper(), index)
        if not {out_label, in_label, balance_label} <= set(by_text):
            continue

        def edge(label):
            index = by_text[label]
            right = row[index]['x1']
            following = row[index + 1] if index + 1 < len(row) else None
            if following is not None and following['text'].strip() in (
                    '($)', '$', '(AUD)'):
                right = following['x1']
            return right

        return edge(out_label), edge(in_label), edge(balance_label)
    return None


def _column_of(word, columns):
    for name, x in zip(('out', 'in', 'balance'), columns):
        if abs(word['x1'] - x) <= TABLE_COLUMN_TOLERANCE:
            return name
    return None


def _anchor_in_balance_column(row, columns):
    for word, value in _row_money(row):
        if _column_of(word, columns) == 'balance':
            return value
    return None


def _anchor_last_figure(row, columns):
    money = _row_money(row)
    return money[-1][1] if money else None


def _anchor_from_neighbour(rows, index, columns):
    """Recover an anchor figure that row grouping split off its own label row.

    ``_rows`` groups by ``round(top)``, so a figure typeset a fraction of a
    point off its label lands in the next row. ANZ's "TOTALS AT END OF PERIOD"
    row keeps its two totals but loses the closing balance that way, which
    rejected five otherwise clean statements.

    Only a neighbour whose sole column figure is in the balance column is
    accepted, so a transaction row -- which always carries an out or in figure
    too -- can never be mistaken for the anchor. ``_reconcile`` still has to
    agree with whatever is recovered.
    """
    for offset in (1, -1):
        neighbour = index + offset
        if not 0 <= neighbour < len(rows):
            continue
        found = None
        for word, value in _row_money(rows[neighbour]):
            column = _column_of(word, columns)
            if column in ('out', 'in'):
                found = None
                break
            if column == 'balance':
                found = value
        if found is not None:
            return found
    return None


COLUMN_TABLE_PROFILES = {
    'westpac': dict(
        labels=('DEBIT', 'CREDIT', 'BALANCE'),
        date=_date_ddmmyy,
        opening_keys=('OPENINGBALANCE',),
        closing_keys=('CLOSINGBALANCE',),
        anchor=_anchor_in_balance_column,
    ),
    'anz': dict(
        labels=('WITHDRAWALS', 'DEPOSITS', 'BALANCE'),
        date=_date_dd_mon,
        opening_keys=('OPENINGBALANCE',),
        # ANZ states its closing figure on the totals row rather than labelling
        # a closing-balance line.
        closing_keys=('TOTALSATENDOFPERIOD',),
        anchor=_anchor_in_balance_column,
    ),
    'bendigo': dict(
        labels=('WITHDRAWALS', 'DEPOSITS', 'BALANCE'),
        date=_date_ddmonyy,
        # Bendigo prints last month's fee summary INSIDE this month's table,
        # between two real transactions, in the same columns: fees, rebate,
        # their totals and a net line. Five figures, no money moved -- the
        # balance is the same before and after. They happen to net to zero
        # here, so reconciliation cannot see them, but they would import as
        # four phantom 1.75 entries and a 0.00, and any month whose rebate
        # did not exactly cover its fee would refuse the whole bundle.
        skip_block=('MONTHLYTRANSACTIONSUMMARY', 'NETTRANSACTIONFEES'),
        # Bendigo states both in its account summary, with the text glued:
        # "Openingbalanceon1Mar2025 $2,772.95".
        opening_keys=('OPENINGBALANCEON', 'OPENINGBALANCE'),
        closing_keys=('CLOSINGBALANCEON', 'CLOSINGBALANCE'),
        anchor=_anchor_last_figure,
    ),
    'bankofmelb': dict(
        # The header reads "Balance$", and the figures align to the "$".
        labels=('DEBIT', 'CREDIT', 'BALANCE$'),
        date=_date_ddmon_in_period,
        hint=_statement_period_hint,
        opening_keys=('OPENINGBALANCE',),
        closing_keys=('CLOSINGBALANCE',),
        # The account summary above each table prints all four of opening,
        # credits, debits and closing on one line, in its own band well clear
        # of the transaction columns. Reading the balance column only ignores
        # it; taking the last figure on the row would read the summary's
        # CLOSING balance as the opening one.
        anchor=_anchor_in_balance_column,
        skip_block=('OPENINGBALANCETOTALCREDITS', 'TRANSACTIONDETAILS'),
        continuation='trailing',
        # The last transaction on a page is followed by a carried-forward
        # subtotal and then a footer -- the bank's ABN, the account number,
        # the statement period. With continuations attaching backwards, that
        # footer would land in the description of the row above it, 195
        # characters of it. The subtotal closes the page's table; the column
        # header on the next page reopens it. The matching "carried forward
        # FROM previous page" line sits AFTER that header, so the marker has
        # to name the direction or page 2 onwards would collect nothing.
        page_end=('CARRIEDFORWARDTONEXTPAGE',),
        # And its mirror on the next page restates the same figure. That one
        # sits inside the table, after the header, so it cannot close
        # anything -- it is simply not a transaction and not a description
        # either: attaching it backwards put it in the last row of the
        # PREVIOUS page.
        skip_rows=('CARRIEDFORWARDFROMPREVIOUSPAGE',),
    ),
    'cba_txn_history': dict(
        # NetBank's "Transaction History" export, which is not a statement:
        # no branding, a different header, and its rows run BACKWARDS.
        labels=('DEBIT', 'CREDIT', 'BALANCE'),
        date=_date_ddmmyyyy,
        # "Report from 11/07/2024  Opening balance  $805.27" -- the label and
        # the figure land in separate rows, so both anchors arrive through
        # neighbour recovery, which accepts only a lone figure in the balance
        # column and so cannot pick up a transaction by mistake.
        opening_keys=('OPENINGBALANCE',),
        closing_keys=('CLOSINGBALANCE',),
        anchor=_anchor_in_balance_column,
        # Its descriptions wrap below their figures, as Bank of Melbourne's do.
        continuation='trailing',
        # Each page ends with a report footer, and the last page adds a totals
        # block. Both would otherwise be read as a continuation of the
        # transaction above them. The header on the next page reopens the
        # table.
        page_end=('PAGENUMBER', 'NO.OFTRANSACTIONS'),
        # This export is the only one that runs newest-first.
        allow_reverse=True,
    ),
}


def _is_descending(txns):
    """True when every dated row is older than the one before it.

    Requires at least two dates to say anything, and one date going the other
    way is enough to answer no -- a document half in order is not a document
    in reverse, and must not be quietly rearranged.
    """
    dates = [t['date'] for t in txns if t.get('date')]
    if len(dates) < 2:
        return False
    return all(a >= b for a, b in zip(dates, dates[1:]))


def parse_column_table_statement(pdf_content, bank):
    """Parse a date/description/out/in/balance statement by coordinates.

    Description rows accumulate until a figure lands in the out or in column,
    and that figure closes the transaction -- so a description spanning two
    rows, or an absent empty-column placeholder, changes nothing.

    Raises StatementParseError if the columns, the anchors or the
    reconciliation fail. Never returns a partial result silently.
    """
    profile = COLUMN_TABLE_PROFILES[bank]
    with pdfplumber.open(io.BytesIO(pdf_content)) as pdf:
        rows = _rows(pdf)

    columns = _table_columns(rows, profile['labels'])
    if not columns:
        raise StatementParseError(
            f"Could not find the {'/'.join(profile['labels'])} column header")

    opening = None
    closing = None
    txns = []
    desc = []
    date = None
    year_hint = None
    # A statement prints more figures than it has transactions, and some land
    # in the same columns: Bendigo follows each period's totals with a
    # fees-and-charges summary, ANZ with a fee summary. Counting those added a
    # 20.00 debit and an offsetting 3.50 pair to one 13-period bundle -- small,
    # but enough to fail reconciliation and have the document refused.
    #
    # The transaction table is bounded by its own header and its totals row, so
    # only rows between the two are transactions. It starts open so a statement
    # whose header this engine cannot see behaves as it did before rather than
    # silently losing every row.
    in_table = True
    in_block = False

    for index, row in enumerate(rows):
        flat = ''.join(w['text'] for w in row).upper()

        # Where a bank states the year its rows belong to differs: ANZ gives a
        # bare year on a row of its own, Bank of Melbourne only the statement
        # period. Either way it is context, not content.
        found_hint = profile.get('hint', _year_only_hint)(row)
        if found_hint is not None:
            year_hint = found_hint

        # A summary block printed inside or above the table: everything from
        # its opening marker to its closing marker is furniture, inclusive.
        # This is tested BEFORE the anchors, because Bank of Melbourne's
        # account summary names an opening and a closing balance on the one
        # line and would otherwise be read as an anchor row.
        block = profile.get('skip_block')
        if block:
            if in_block:
                if block[1] in flat:
                    in_block = False
                desc, date = [], None
                continue
            if block[0] in flat:
                in_block = True
                desc, date = [], None
                continue

        if any(key in flat for key in profile['opening_keys']):
            if opening is None:
                opening = (profile['anchor'](row, columns)
                           or _anchor_from_neighbour(rows, index, columns))
            desc, date = [], None
            in_table, in_block = True, False
            continue
        if any(key in flat for key in profile['closing_keys']):
            found = (profile['anchor'](row, columns)
                     or _anchor_from_neighbour(rows, index, columns))
            if found is not None:
                closing = found
            desc, date = [], None
            in_table = False
            continue

        # The column header reopens the table: on every page, and on every
        # period of a multi-period bundle.
        if all(label in flat for label in profile['labels']):
            desc, date = [], None
            # A summary block cannot span into the next table, so an unclosed
            # one ends here rather than swallowing every later row.
            in_table, in_block = True, False
            continue
        # Per-page subtotals restate figures already counted.
        if 'TOTALSATENDOFPAGE' in flat:
            desc, date = [], None
            continue
        if any(key in flat for key in profile.get('page_end', ())):
            desc, date = [], None
            in_table = False
            continue
        if any(key in flat for key in profile.get('skip_rows', ())):
            desc, date = [], None
            continue

        if _is_furniture(flat):
            continue

        if date is None:
            found_date = profile['date'](row, year_hint)
            if found_date:
                date = found_date

        movement = None
        balance = None
        for word, value in _row_money(row):
            column = _column_of(word, columns)
            # The column says which direction the money moved; the figure's
            # own sign says whether this row REVERSES that direction. Bendigo
            # prints a reversed OSKO payment as "-10.00" under Withdrawals,
            # with the balance beside it rising 10.00. Forcing the sign from
            # the column alone booked that as a debit and left a 31-page,
            # thirteen-period bundle 20.00 short, so the gate refused all of
            # it. Negating rather than taking abs() reads both cases from the
            # one rule, and the reconciliation gate remains the arbiter.
            if column == 'out':
                movement = -value
            elif column == 'in':
                movement = value
            elif column == 'balance':
                balance = value

        keep = []
        for word in row:
            text = word['text']
            if TABLE_MONEY_RE.match(text) and _column_of(word, columns):
                continue
            keep.append(text)
        line = ' '.join(keep)

        # Where a wrapped description sits relative to its figures is the one
        # thing these statements do not agree on. Westpac and ANZ wrap BEFORE
        # the figures, so lines accumulate until a figure closes them. Bank of
        # Melbourne wraps AFTER: "Transfer to CBA account" is printed on the
        # line below the withdrawal it explains. Accumulating those forward
        # attached every continuation to the FOLLOWING transaction -- each
        # description one row late, and the reference line of one payment
        # filed against the next.
        if (profile.get('continuation') == 'trailing'
                and movement is None and date is None
                and in_table and txns and line):
            txns[-1]['description'] = _truncate(
                f"{txns[-1]['description']} {line}".strip(), 200)
            continue

        desc.append(line)

        if movement is not None and in_table:
            txns.append({
                'date': date,
                'description': _truncate(
                    ' '.join(d for d in desc if d).strip(), 200),
                'amount': movement,
                'balance': balance,
            })
            desc, date = [], None

    if opening is None or closing is None:
        raise StatementParseError(
            f"Could not read the opening and closing balance for this "
            f"{bank} statement")

    if profile.get('allow_reverse') and _is_descending(txns):
        # Read newest-first, the rows contradict every downstream assumption:
        # the running balance does not follow row to row, the dates run
        # backwards, and the import gate refuses both -- correctly, because it
        # cannot tell a document in reverse from a document with rows out of
        # order. Turning it round here means one canonical direction
        # afterwards. Only a document descending the WHOLE way is turned; a
        # genuinely mixed one is left as it is, and still refused.
        txns.reverse()

    _reconcile(txns, opening, closing)

    return {
        'opening_balance': opening,
        'closing_balance': closing,
        'account_name': '',
        'bsb': '',
        'account_number': '',
        'period_start': '',
        'period_end': '',
        'transactions': txns,
    }


# --------------------------------------------------------------------------
# Direct-parse verification
# --------------------------------------------------------------------------

# A figure is read as belonging to a column when its right edge lands this
# close to that column header's right edge. Measured from real NAB statements,
# where figures scatter across x=394/396 under a "Debits" header ending at 396.
COLUMN_TOLERANCE = 6.0


def _nab_column_anchors(words):
    """Right edges of this page's "Debits" and "Credits" column headers.

    The anchor must come from the transaction table's header ROW, not from any
    word that happens to read "Debits". Both real exemplars close with "Bank
    Accounts Debits (BAD) Tax or State Debits Duty has been ...", running text
    that sits well left of the table -- anchoring on the last matching word put
    the debit column at x=208 and rejected a clean statement.

    Returns (debit_x, credit_x), either of which may be None.
    """
    rows = defaultdict(list)
    for word in words:
        rows[round(word['top'] / 3)].append(word)

    for row in rows.values():
        by_text = {}
        for word in row:
            by_text.setdefault(word['text'].strip().rstrip(':'), word)
        # "Particulars" is what distinguishes the table header from prose.
        if {'Particulars', 'Debits', 'Credits'} <= set(by_text):
            return by_text['Debits']['x1'], by_text['Credits']['x1']

    return None, None


def _nab_column_signs(pdf_content):
    """The sign of every transaction figure, read from the page itself.

    NAB prints debits and credits in separate right-aligned columns, so the
    column a figure sits in states its sign outright. Returns a list of
    (amount, column_name) in reading order, or None when the statement has no
    column headers to anchor against.
    """
    signs = []
    anchored = False
    debit_x = credit_x = None

    with pdfplumber.open(io.BytesIO(pdf_content)) as pdf:
        for page in pdf.pages:
            # extract_words is the expensive call here — on a 14-page
            # statement it dominates the check — so each page is read once and
            # both the header anchors and the figures come off that one pass.
            page_words = page.extract_words()
            page_debit_x, page_credit_x = _nab_column_anchors(page_words)
            # Headers repeat on every transaction page; a page without them
            # (a continuation) keeps the last page's columns.
            if page_debit_x is not None:
                debit_x = page_debit_x
            if page_credit_x is not None:
                credit_x = page_credit_x
            if debit_x is None or credit_x is None:
                continue
            anchored = True

            figures = [w for w in page_words if MOVE_RE.match(w['text'])]
            figures.sort(key=lambda w: (round(w['top'], 1), w['x0']))
            for word in figures:
                amount = float(word['text'].replace(',', ''))
                if abs(word['x1'] - debit_x) <= COLUMN_TOLERANCE:
                    signs.append((-amount, 'debit'))
                elif abs(word['x1'] - credit_x) <= COLUMN_TOLERANCE:
                    signs.append((amount, 'credit'))

    return signs if anchored else None


def verify_nab_columns(pdf_content, transactions):
    """Check a NAB parse against the debit/credit columns it was printed in.

    parse_nab_statement reads flat text, which discards the column a figure
    was printed in, and recovers signs by subset-sum against each day's
    closing balance. That has a silent fallback cascade -- ambiguous tie,
    then a relaxed 1.00 tolerance, then a greedy assignment -- and one case
    reconciliation provably cannot catch: two equal figures on opposite sides
    of the same day. Both assignments foot, so opening + sum == closing holds
    either way. The columns are the only witness.

    Raises StatementParseError on disagreement. Returns None when the
    statement carries no column headers to check against -- absence of
    evidence, not evidence of a fault.

    Cost: this re-opens the PDF, which roughly doubles NAB parse time (6.1s ->
    11.4s measured on a real 14-page, 413-transaction statement). Reading the
    words once and sharing them with the parser would need _extract_all_text to
    change shape, and it has eight callers across banks with no exemplars to
    test against -- not a trade worth making inside a guardrail change.
    """
    expected = _nab_column_signs(pdf_content)
    if expected is None:
        return None

    parsed = [t['amount'] for t in transactions]
    if len(expected) != len(parsed):
        raise StatementParseError(
            f"Column cross-check failed: the page shows {len(expected)} "
            f"transaction figures, the parser returned {len(parsed)}"
        )

    for index, ((want, column), got) in enumerate(zip(expected, parsed)):
        if round(want, 2) == round(got, 2):
            continue
        description = transactions[index].get('description', '')
        raise StatementParseError(
            f"Column cross-check failed on row {index + 1} "
            f"({description!r}): printed in the {column} column as "
            f"{abs(want):,.2f}, parsed as {got:+,.2f}"
        )

    return None


class StatementNotImportable(Exception):
    """Raised when a statement must not become an import without a decision.

    Carries ``reason`` -- the text shown to whoever has to decide.
    """

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def _chain_break_indexes(rows, opening, tolerance=0.01):
    """Indexes where a row's balance does not follow from the row before it.

    Rows printing no balance are skipped rather than counted as breaks: not
    every parser emits a balance column, and the absence of one is not a fault.
    """
    breaks = []
    previous = opening
    for i, row in enumerate(rows):
        balance = row.get('balance')
        if balance is None:
            continue
        amount = row.get('amount')
        if previous is not None and amount is not None:
            if abs((float(previous) + float(amount)) - float(balance)) > tolerance:
                breaks.append(i)
        previous = balance
    return breaks


def _date_order_breaks(rows):
    """Indexes where the statement's dates run backwards.

    Checked separately from the balances because the two fail independently: a
    page read with the wrong year keeps a perfect balance chain while every date
    on it is twelve months out. That is exactly how 53 rows worth 22,399.41
    were dropped from an import with every balance check passing.
    """
    from datetime import datetime

    def parsed(value):
        if not value:
            return None
        for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d/%m/%y'):
            try:
                return datetime.strptime(str(value).strip(), fmt).date()
            except ValueError:
                continue
        return None

    breaks = []
    previous = None
    for i, row in enumerate(rows):
        this = parsed(row.get('date'))
        if this is None:
            continue
        if previous is not None and this < previous:
            breaks.append(i)
        previous = this
    return breaks


def assert_importable(rows, opening, closing, tolerance=0.01):
    """Refuse a statement whose own figures say the parse is wrong.

    This is the last check before rows become a ReviewJob, and it is
    deliberately recomputed from the rows being imported rather than trusting a
    flag set further upstream: the figures are what matter, and a flag can be
    lost in a session round-trip or edited by a client.

    ``verify_direct_parse`` already rejects a bad *parse*, but it runs before
    the period filter and it treats a statement with no anchors as merely
    unverified. Both gaps were load-bearing in a real incident: a statement
    reconciled cleanly across all 240 of its rows, then had 130 of them removed
    by the financial-year filter, and the 110 that were imported carried the
    whole statement's opening and closing balances. Reconciliation had already
    passed, so nothing objected.

    Raises StatementNotImportable. Callers turn that into a refusal that the
    user can consciously override, never into a silent warning.
    """
    if not rows:
        return True

    has_opening = opening not in (None, '', 0, 0.0)
    has_closing = closing not in (None, '', 0, 0.0)
    if not has_opening and not has_closing:
        raise StatementNotImportable(
            "No opening or closing balance could be read from this statement, "
            "so its transactions cannot be checked against it. On the formats "
            "we parse, a missing balance normally means the statement was read "
            "incorrectly rather than that it prints no balance."
        )

    movements = sum(float(r.get('amount') or 0) for r in rows)
    derived = round(float(opening or 0) + movements, 2)
    if abs(derived - float(closing or 0)) > tolerance:
        raise StatementNotImportable(
            f"These {len(rows)} transactions do not add up to the statement's "
            f"own balances: opening {float(opening or 0):,.2f} plus movements "
            f"{movements:,.2f} comes to {derived:,.2f}, but the closing balance "
            f"is {float(closing or 0):,.2f} "
            f"(out by {derived - float(closing or 0):+,.2f}). "
            f"Either a transaction is missing, duplicated or the wrong way "
            f"round, or the balances belong to a wider period than the rows."
        )

    chain = _chain_break_indexes(rows, opening, tolerance)
    if chain:
        first = rows[chain[0]]
        raise StatementNotImportable(
            f"The running balance does not follow row to row at "
            f"{len(chain)} point(s) — the first is {first.get('date')} "
            f"'{str(first.get('description'))[:40]}'. That means rows are "
            f"missing or duplicated, most often because a page is absent from "
            f"the file. Check the statement covers every page."
        )

    disorder = _date_order_breaks(rows)
    if disorder:
        row = rows[disorder[0]]
        raise StatementNotImportable(
            f"The dates run backwards at {len(disorder)} point(s) — the first "
            f"is {row.get('date')}. A bank statement runs forward in time, so "
            f"a date going back means a date was misread; a page given the "
            f"wrong year leaves the balances looking perfectly correct."
        )

    return True


def is_dormant_statement(result, tolerance=0.01):
    """True when no transactions is the statement's own answer, not a failure.

    An account can go a whole period without a single transaction -- a dormant
    SMSF cash account holding 0.01 is the exemplar -- and its statement then
    prints an opening balance, a closing balance equal to it, and an empty
    table. That parses to an empty list, which is indistinguishable from
    "could not read this file" unless the balances are consulted: they agree,
    and between them they account for every cent of the period.

    Both upload paths treated an empty parse as a failed one and sent the file
    to Vision OCR. That costs an API call and half a minute to re-read a
    statement already read correctly, and it asks a model to find transactions
    in a document that has none -- which is how a transaction gets invented.
    """
    if not result or result.get('transactions'):
        return False
    opening = result.get('opening_balance')
    closing = result.get('closing_balance')
    if opening is None or closing is None:
        return False
    try:
        return abs(float(closing) - float(opening)) <= tolerance
    except (TypeError, ValueError):
        return False


def verify_direct_parse(result, bank, pdf_content=None, filename=""):
    """Check a direct parse before it is allowed to become an import.

    Until this existed, ``_reconcile`` ran on exactly one path -- the Claude
    Vision OCR fallback, which only fires after the direct parser has already
    raised or returned nothing. A statement that parsed to plausible-but-wrong
    figures was never balance-checked, so a mis-signed import landed clean and
    the preview was the only thing standing in its way.

    Raises StatementParseError when the statement contradicts its own printed
    balances. Both callers of the direct parser treat that as the trigger for
    the Vision fallback, so a rejected statement is re-read rather than lost.

    A statement carrying no balance anchors is flagged ``unverified`` instead:
    there is nothing to check it against, and refusing it would punish the
    absence of evidence rather than a fault.
    """
    # Stamp the detected format before anything returns early: no parser sets
    # it, so the preview badge read "Unknown Bank" for every statement of every
    # bank. It is the one place the operator can see what claimed the file.
    if isinstance(result, dict):
        from .pdf_parsers import bank_label
        result.setdefault('bank', bank)
        result.setdefault('bank_label', bank_label(bank))

    if not result or not result.get('transactions'):
        return result

    opening = result.get('opening_balance')
    closing = result.get('closing_balance')

    if not opening and not closing:
        result['unverified'] = True
        result['reconciliation_warning'] = (
            'No opening or closing balance was found on this statement, so its '
            'transactions could not be checked against it.'
        )
        logger.warning(
            f'Direct parse unverified: file={filename} bank={bank} — '
            f'no balance anchors to reconcile against '
            f'({len(result["transactions"])} transactions)'
        )
        return result

    try:
        _reconcile(result['transactions'], float(opening), float(closing))
        if bank == 'nab' and pdf_content is not None:
            verify_nab_columns(pdf_content, result['transactions'])
    except StatementParseError as err:
        logger.warning(
            f'Direct parse rejected: file={filename} bank={bank} — {err}'
        )
        raise

    return result
