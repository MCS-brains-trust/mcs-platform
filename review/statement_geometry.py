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
    """'1,853.60' -> 1853.60"""
    return float(s.replace(',', ''))


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


def _money_columns(rows, min_count=None, gap=12.0):
    """
    Auto-detect debit/credit column x1 centres by clustering bare-amount right-edges.
    Returns (debit_x, credit_x) or None if two distinct clusters cannot be found.

    ``min_count`` is the minimum cluster population to count as a money column.
    When not supplied it scales with the number of amount tokens (floor 2) so
    low-activity statements (only a handful of transactions) are not rejected,
    while a fixed high threshold no longer over-filters them.
    """
    xs = sorted(w['x1'] for row in rows for w in row if MOVE_RE.match(w['text']))
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


def _text_only(row):
    """
    Description text from a row: drop pure-amount, CR/DR, and date tokens.
    Date tokens (e.g. '31Oct') are excluded so they don't contaminate descriptions.
    """
    keep = []
    for w in row:
        t = w['text']
        if MOVE_RE.match(t):
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
        if not all(MOVE_RE.match(t) or t in ('CR', 'DR') for t in texts):
            continue
        amounts = [t for t in texts if MOVE_RE.match(t)]
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

    cols = _money_columns(rows)
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
        if row and date is None and DATE_RE.match(row[0]['text']):
            dm = DATE_RE.match(row[0]['text'])
            day = dm.group(1).zfill(2)
            month_str = dm.group(2).capitalize()[:3]
            month_num = int(MONTH_MAP[month_str])
            # Dec→Jan year rollover (9a92915 logic)
            if prev_month is not None and prev_month == 12 and month_num == 1:
                cur_year = str(int(cur_year) + 1)
            prev_month = month_num
            date = f"{cur_year}-{MONTH_MAP[month_str]}-{day}"

        # Identify whether this row carries a movement amount and in which column.
        movement = None
        for w in row:
            if MOVE_RE.match(w['text']):
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
