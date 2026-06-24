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
import re
from collections import defaultdict

import pdfplumber


class StatementParseError(Exception):
    """Raised when a statement cannot be parsed or fails reconciliation."""


# Bare monetary amount — no currency symbol, no CR/DR: "1,234.56"
MOVE_RE = re.compile(r'^\d{1,3}(,\d{3})*\.\d{2}$')
# Running-balance token: "1,234.56 CR" or "1,234.56 DR"
BAL_RE = re.compile(r'(\d{1,3}(?:,\d{3})*\.\d{2})\s*(CR|DR)')
# Glued CBA date token: "31Oct" or "31Oct2025"
DATE_RE = re.compile(r'^(\d{1,2})(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)', re.IGNORECASE)
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


def _signed_balance(text):
    """Extract signed balance from text containing 'amount CR/DR'. CR=positive, DR=negative."""
    m = BAL_RE.search(text)
    if not m:
        return None
    return _f(m.group(1)) * (1 if m.group(2) == 'CR' else -1)


def _rows(pdf):
    """Return word-rows (sorted lists) top-to-bottom across all pages, stopping after CLOSING BALANCE."""
    out = []
    for page in pdf.pages:
        lines = defaultdict(list)
        for w in page.extract_words():
            lines[round(w['top'])].append(w)
        for top in sorted(lines):
            row = sorted(lines[top], key=lambda w: w['x0'])
            out.append(row)
            if 'CLOSINGBALANCE' in ''.join(w['text'] for w in row):
                return out
    return out


def _money_columns(rows, min_count=5, gap=12.0):
    """
    Auto-detect debit/credit column x1 centres by clustering bare-amount right-edges.
    Returns (debit_x, credit_x) or None if two distinct clusters cannot be found.
    """
    xs = sorted(w['x1'] for row in rows for w in row if MOVE_RE.match(w['text']))
    if len(xs) < 2:
        return None
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
        if DATE_RE.match(t):
            continue
        keep.append(t)
    return ' '.join(keep)


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
    for row in rows:
        flat = ''.join(w['text'] for w in row)
        if 'OPENINGBALANCE' in flat:
            joined = ' '.join(w['text'] for w in row)
            opening = _signed_balance(joined)
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

    for row in rows:
        flat = ''.join(w['text'] for w in row)
        joined = ' '.join(w['text'] for w in row)

        if 'OPENINGBALANCE' in flat:
            continue
        if 'CLOSINGBALANCE' in flat:
            closing = _signed_balance(joined)
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
                'description': ' '.join(d for d in desc if d).strip(),
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
