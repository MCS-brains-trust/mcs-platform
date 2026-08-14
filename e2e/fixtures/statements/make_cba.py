"""Generate the committed CBA statement fixture.

The PDF is a build artifact of this script, not a hand-made binary, so a reviewer
reads a transaction table instead of an opaque blob. Regenerate with:

    venv/bin/python e2e/fixtures/statements/make_cba.py

This is routed to the CBA parser by a bare substring match on the bank name in
the preamble text -- detect_bank (review/pdf_parsers.py:1741) sets
is_cba = "commonwealth bank" in text_lower or "commbank" in text_lower, and
that alone selects "cba" a few lines later. The "Commonwealth Bank of
Australia" line drawn below is what does the routing; nothing else here does.

The header's column spacing is NOT load-bearing for routing. detect_bank's one
whitespace regex, r"date\\s+transaction\\s+details\\s+amount\\s+balance",
discriminates a different CBA variant (the NetBank "Transaction Listing"
export, bank code "cba_txn_listing") from the standard bank-statement layout
this fixture reproduces -- and this fixture's header ("Date    Transaction
Debit    Credit    Balance") never matches that pattern regardless of spacing.
Copying this file for a new bank: route on whatever substring or pattern that
bank's own detect_bank branch actually keys on, not on reproducing this
header's whitespace.

One property below IS true of real CBA statements and is load-bearing: the
date column is drawn tight enough that "02 Oct" extracts as "02Oct", which is
the glued form review/statement_geometry.py's DATE_RE expects.

The running balance is drawn ONLY on the OPENING BALANCE and CLOSING BALANCE
anchor rows, not on every transaction row, even though a real CBA statement
shows one on every line. parse_cba_geometry never reads a per-transaction
balance -- it derives the total purely from opening + sum(movements) ==
closing -- so a per-row balance figure is pure decoration that this parser's
own logic cannot render safely both ways at once:

  * drawn as "11,100.00 CR" (a real space) it lands in pdfplumber as two
    words, and the bare "11,100.00" matches MOVE_RE like any debit/credit
    figure. It appears on every row (8 times here, once per anchor plus one
    per transaction), out-populating the real 3-member debit and credit
    clusters in _money_columns, so the balance column gets mistaken for the
    credit column and every real movement is overwritten by the running
    total (caught by test_the_statement_reconciles: movements summed to
    70562 instead of 3228).
  * drawn glued as "11,100.00CR" (no space, one word) it stops matching
    MOVE_RE -- fixing the above -- but now survives _text_only's filter
    (which only drops bare MOVE_RE amounts, literal "CR"/"DR", and date
    tokens) and is appended to every transaction's description instead
    (caught by test_debits_are_negative_and_credits_positive: description
    keys carried a trailing " 11,100.00CR").

Anchor rows don't have this conflict: 'OPENINGBALANCE'/'CLOSINGBALANCE' rows
are consumed by dedicated `continue` branches before _text_only ever sees
them, so their balance figures never risk polluting a description.

The preamble ("Commonwealth Bank of Australia") carries a "Page 1 of 1"
marker on the same row deliberately, matching _FURNITURE_PAGE_RE. Without
it, that row is not furniture by any of the parser's keyword checks, so it
would be swept into `desc` and prefix the first transaction's description
(nothing resets the buffer between the preamble and the first transaction
row except a movement being found).

Invariant mode is required: reportlab embeds a /CreationDate by default and no
two runs would produce the same bytes.
"""
import io
import os

import reportlab.rl_config as rl_config

rl_config.invariant = 1

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

OUT_PATH = os.path.join(os.path.dirname(__file__), "cba_sample.pdf")

OPENING = 10000.00
YEAR = "2025"

# (date, description, debit, credit)
TRANSACTIONS = [
    ("02Oct", "EFTPOS SALES INV 1001", None, 1100.00),
    ("05Oct", "OFFICE SUPPLIES PTY LTD", 550.00, None),
    ("12Oct", "BANK FEES AND CHARGES", 22.00, None),
    ("18Oct", "CONSULTING FEE INV 1002", None, 2200.00),
    ("24Oct", "FRESH FOOD SUPPLIES", 300.00, None),
    ("28Oct", "EXPORT SALE INV 1003", None, 800.00),
]

X_DATE, X_DESC, X_DEBIT, X_CREDIT, X_BALANCE = 40, 95, 300, 380, 470
LINE_HEIGHT = 18


def _money(value):
    return f"{value:,.2f}"


def build_pdf():
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, invariant=1)
    c.setFont("Helvetica", 9)
    y = 780

    c.drawString(X_DATE, y, "Commonwealth Bank of Australia    Page 1 of 1")
    y -= LINE_HEIGHT
    c.drawString(X_DATE, y, "Account Number 06 2000 12345678")
    y -= LINE_HEIGHT * 2

    # Column spacing here is cosmetic, not routing: detect_bank selects "cba"
    # from the preamble's bank-name substring above, before this header is
    # ever inspected. Real spaces are kept only because a real CBA statement
    # has them, not because anything downstream requires it.
    c.drawString(X_DATE, y, "Date    Transaction    Debit    Credit    Balance")
    y -= LINE_HEIGHT

    balance = OPENING
    c.drawString(X_DATE, y, "01Oct")
    c.drawString(X_DESC, y, f"{YEAR} OPENING BALANCE")
    c.drawString(X_BALANCE, y, f"{_money(balance)}CR")
    y -= LINE_HEIGHT

    for date, desc, debit, credit in TRANSACTIONS:
        c.drawString(X_DATE, y, date)
        c.drawString(X_DESC, y, desc)
        if debit is not None:
            c.drawString(X_DEBIT, y, _money(debit))
            balance -= debit
        if credit is not None:
            c.drawString(X_CREDIT, y, _money(credit))
            balance += credit
        y -= LINE_HEIGHT

    c.drawString(X_DATE, y, "31Oct")
    c.drawString(X_DESC, y, "CLOSING BALANCE")
    c.drawString(X_BALANCE, y, f"{_money(balance)}CR")

    c.save()
    return buf.getvalue()


if __name__ == "__main__":
    with open(OUT_PATH, "wb") as fh:
        fh.write(build_pdf())
    print(f"wrote {OUT_PATH}")
