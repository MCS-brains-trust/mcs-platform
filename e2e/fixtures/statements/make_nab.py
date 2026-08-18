"""Generate the committed NAB statement fixture.

Regenerate with:

    python3 e2e/fixtures/statements/make_nab.py

Synthesised from the geometry of two real NAB Business Everyday Account
statements (413 and 370 transactions). The real statements are client data and
are NOT in this repository; only the measurements below were taken from them.

Routing: detect_bank (review/pdf_parsers.py:1758) selects "nab" on a bare
substring match, ``"national australia bank" in text_lower``. The
"National Australia Bank Limited" line in the preamble is what routes this
fixture; nothing else here does.

Three properties ARE load-bearing, all measured from the real statements:

1. **The three money columns are right-aligned at distinct x.** Debits end at
   x=396, credits at x=467, the running balance at x=538, and the "Debits" and
   "Credits" column headers end at the same x as the figures beneath them.
   verify_nab_columns reads the sign of every transaction from which column its
   figure sits in, so these positions are the whole point of the fixture. In
   the real statements the figures scatter across x=394/396 and x=465/467,
   which is why the check matches on a tolerance rather than an exact x.

2. **The running balance appears once per DAY, not once per row.** NAB prints
   it against the last transaction of each date group. parse_nab_statement
   depends on this: it queues each day's unsigned figures and resolves their
   signs by subset-sum against the day's closing balance. A per-row balance
   would give every transaction its own checkpoint and the subset-sum would
   never have to choose.

3. **Descriptions are dot-filled to the amount.** The parser's row regexes
   (NAB_AMT_ONLY_RE, NAB_AMT_BAL_RE) key on ``\\.{2,}`` before the figure. A
   row without dot filler is not a transaction row to this parser.

``ambiguous_day=True`` adds the case that motivated the positional check: a
$100.00 debit and a $100.00 credit on one day. Both sign assignments reconcile
to the same day-end balance, so the subset-sum has two answers, warns
"ambiguous subset-sum", and keeps the first — which is the wrong one here. The
statement still foots to the cent either way, so reconciliation cannot see the
error. Only the column positions can.

Invariant mode is required: reportlab embeds a /CreationDate by default and no
two runs would produce the same bytes.
"""
import io
import os

import reportlab.rl_config as rl_config

rl_config.invariant = 1

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

OUT_PATH = os.path.join(os.path.dirname(__file__), "nab_sample.pdf")

OPENING = 10000.00

# Column right edges, measured from the real statements.
X_DATE, X_DESC = 40, 103
X_DEBIT, X_CREDIT, X_BALANCE = 396, 467, 538
X_SUMMARY = 282  # page-1 summary figures sit well clear of the money columns
LINE_HEIGHT = 16

# (date, [(description, debit, credit), ...]) — one balance per day group.
DAYS = [
    ("2 May 2026", [("EFTPOS SALES INV 1001", None, 1100.00)]),
    ("5 May 2026", [
        ("OFFICE SUPPLIES PTY LTD", 550.00, None),
        ("BANK FEES AND CHARGES", 22.00, None),
    ]),
    ("12 May 2026", [
        ("CONSULTING FEE INV 1002", None, 2200.00),
        ("FRESH FOOD SUPPLIES", 300.00, None),
    ]),
    ("18 May 2026", [("TOYOTA FINANCE 009090", 200.00, None)]),
]

# The tie: same figure on both sides of the same day, so the day's net change
# is zero under either assignment.
AMBIGUOUS_DAY = ("21 May 2026", [
    ("HARDWARE SUPPLIES PAKENHAM", 100.00, None),
    ("REFUND HARDWARE SUPPLIES", None, 100.00),
])


def _money(value):
    return f"{value:,.2f}"


def _dotted(description, width=58):
    """Description dot-filled to the amount, as NAB prints it."""
    return description + "." * max(2, width - len(description))


def build_pdf(ambiguous_day=False, tamper_closing=None, omit_anchors=False,
              trailing_prose=False):
    """Build a NAB statement PDF.

    ambiguous_day:  add the equal-debit-and-credit day described above.
    tamper_closing: override the printed closing balance, so the statement
                    no longer foots against its own transactions.
    omit_anchors:   omit the opening and closing balance lines entirely, so
                    there is nothing to reconcile against.
    trailing_prose: add the real statements' closing note, whose wording puts
                    the word "Debits" in running text well left of the Debits
                    column. Verbatim from page 14 of both exemplars.
    """
    days = list(DAYS)
    if ambiguous_day:
        days.append(AMBIGUOUS_DAY)

    closing = OPENING
    total_debits = 0.0
    total_credits = 0.0
    for _, rows in days:
        for _, debit, credit in rows:
            if debit is not None:
                closing -= debit
                total_debits += debit
            if credit is not None:
                closing += credit
                total_credits += credit

    printed_closing = closing if tamper_closing is None else tamper_closing

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, invariant=1)
    c.setFont("Helvetica", 9)
    y = 800

    c.drawString(X_DATE, y, "Account Balance Summary")
    y -= LINE_HEIGHT
    if not omit_anchors:
        c.drawString(X_DATE, y, "Opening balance")
        c.drawRightString(X_SUMMARY, y, f"${_money(OPENING)} Cr")
        y -= LINE_HEIGHT
    c.drawString(X_DATE, y, "Total credits")
    c.drawRightString(X_SUMMARY, y, f"${_money(total_credits)}")
    y -= LINE_HEIGHT
    c.drawString(X_DATE, y, "Total debits")
    c.drawRightString(X_SUMMARY, y, f"${_money(total_debits)}")
    y -= LINE_HEIGHT
    if not omit_anchors:
        c.drawString(X_DATE, y, "Closing balance")
        c.drawRightString(X_SUMMARY, y, f"${_money(printed_closing)} Cr")
        y -= LINE_HEIGHT

    c.drawString(X_DATE, y, "Statement starts 2 May 2026")
    y -= LINE_HEIGHT
    c.drawString(X_DATE, y, "Statement ends 31 May 2026")
    y -= LINE_HEIGHT

    # The routing line. "National Australia Bank Limited" is what makes
    # detect_bank return "nab".
    c.drawString(
        X_DATE, y,
        "Statement number 115 National Australia Bank Limited "
        "ABN 12 004 044 937 Page 1 of 1",
    )
    y -= LINE_HEIGHT
    c.drawString(X_DATE, y, "NAB Business Everyday Account")
    y -= LINE_HEIGHT
    c.drawString(X_DATE, y, "BSB number 083-004")
    y -= LINE_HEIGHT
    c.drawString(X_DATE, y, "Account number 12345678")
    y -= LINE_HEIGHT * 2

    # Column header. The right edges of "Debits" and "Credits" mark the
    # columns that verify_nab_columns reads the signs from.
    c.drawString(X_DATE, y, "Date")
    c.drawString(X_DESC, y, "Particulars")
    c.drawRightString(X_DEBIT, y, "Debits")
    c.drawRightString(X_CREDIT, y, "Credits")
    c.drawRightString(X_BALANCE + 12, y, "Balance")
    y -= LINE_HEIGHT

    c.drawString(X_DESC, y, "Brought forward")
    c.drawRightString(X_BALANCE, y, _money(OPENING))
    c.drawString(X_BALANCE + 5, y, "Cr")
    y -= LINE_HEIGHT

    balance = OPENING
    for date, rows in days:
        for index, (description, debit, credit) in enumerate(rows):
            if index == 0:
                c.drawString(X_DATE, y, date)
            c.drawString(X_DESC, y, _dotted(description))
            if debit is not None:
                c.drawRightString(X_DEBIT, y, _money(debit))
                balance -= debit
            if credit is not None:
                c.drawRightString(X_CREDIT, y, _money(credit))
                balance += credit
            # Running balance on the last row of the day only.
            if index == len(rows) - 1:
                c.drawRightString(X_BALANCE, y, _money(balance))
                c.drawString(X_BALANCE + 5, y, "Cr")
            y -= LINE_HEIGHT

    if trailing_prose:
        y -= LINE_HEIGHT
        c.drawString(
            X_DATE, y,
            "Bank Accounts Debits (BAD) Tax or State Debits Duty has been "
            "For information on resolving",
        )

    c.save()
    return buf.getvalue()


if __name__ == "__main__":
    with open(OUT_PATH, "wb") as fh:
        fh.write(build_pdf())
    print(f"wrote {OUT_PATH}")
