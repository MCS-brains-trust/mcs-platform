"""Regression tests for a CBA statement that the geometry parser rejected.

A real 12-page statement (1 May - 30 Jul 2026, 240 transactions) was rejected
with "Missing opening/closing balance anchor" even though every transaction
extracted cleanly. Its closing figure was typeset 0.675pt above the
CLOSINGBALANCE keyword, and ``_rows`` groups words by ``round(top)``, so
155.199 rounded to 155 while the keyword rounded to 156: the keyword row was
left with no amount on it at all.

The consequences ran well past a failed parse. The rejection dropped the upload
into the Claude Vision fallback, which reads two pages at a time; pages 5-6 of
that statement contained no year token anywhere, so Vision dated 53 June rows
to 2025. Their balances still chained perfectly, so nothing downstream could
see it, and the financial-year filter then discarded all 53 as out of period --
22,399.41 of real movement gone, with a closing balance that could not
reconcile.

Every PDF here is synthesised in-process, so no test depends on a client
statement.
"""
import io

import reportlab.rl_config as rl_config

rl_config.invariant = 1

from django.test import SimpleTestCase
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

import pdfplumber

from .statement_geometry import (
    StatementParseError,
    _rows,
    parse_cba_geometry,
)

# The offset measured on the real statement: enough for round(top) to split the
# figure off its own label, far less than the gap to the next line.
ANCHOR_SPLIT_OFFSET = 0.675

OPENING = 10000.00
X_DATE, X_DESC, X_DEBIT, X_CREDIT, X_BALANCE = 40, 95, 300, 380, 470
LINE_HEIGHT = 18

# (date, description, debit, credit) -- three of each so _money_columns has two
# populous clusters to find.
TRANSACTIONS = [
    ("02Oct", "EFTPOS SALES INV 1001", None, 1100.00),
    ("05Oct", "OFFICE SUPPLIES PTY LTD", 550.00, None),
    ("12Oct", "BANK FEES AND CHARGES", 22.00, None),
    ("18Oct", "CONSULTING FEE INV 1002", None, 2200.00),
    ("24Oct", "FRESH FOOD SUPPLIES", 300.00, None),
    ("28Oct", "EXPORT SALE INV 1003", None, 800.00),
]

# Drawn as one string with no spaces, so pdfplumber returns a single word --
# how a real CBA statement's tight date column arrives.
GLUED_DATE_ROW = "30OctDIRECTCREDITMCAREBENEFITS"
GLUED_DATE_CREDIT = 450.00


def _money(value):
    return f"{value:,.2f}"


def build_pdf(split_closing_anchor=False, page_furniture=False,
              glued_date_row=False):
    """A minimal CBA statement, with each defect switchable.

    ``split_closing_anchor`` lifts the closing figure off its keyword row.
    ``page_furniture`` draws the header pieces that a real statement leaves
    stranded in rows of their own once round(top) has split them.
    ``glued_date_row`` adds a transaction whose date and description arrive as
    a single word.
    """
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, invariant=1)
    c.setFont("Helvetica", 9)
    y = 780

    c.drawString(X_DATE, y, "Commonwealth Bank of Australia    Page 1 of 1")
    y -= LINE_HEIGHT

    if page_furniture:
        # None of these match a furniture pattern on their own, and each one
        # was observed stranded in a row of its own on the real statement.
        for stray in ("Your Statement", "Statement11", "06269281523335"):
            c.drawString(X_DATE, y, stray)
            y -= LINE_HEIGHT

    c.drawString(X_DATE, y, "Account Number 06 2000 12345678")
    y -= LINE_HEIGHT * 2

    if page_furniture:
        # The column header, split as it is on 10 pages out of 12: the 'Date'
        # cell sits a fraction of a point off the rest of the header.
        c.drawString(X_DESC, y, "Transaction    Debit    Credit    Balance")
        c.drawString(X_DATE, y - ANCHOR_SPLIT_OFFSET, "Date")
    else:
        c.drawString(X_DATE, y, "Date    Transaction    Debit    Credit    Balance")
    y -= LINE_HEIGHT

    balance = OPENING
    c.drawString(X_DATE, y, "01Oct")
    c.drawString(X_DESC, y, "2025 OPENING BALANCE")
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

    if glued_date_row:
        c.drawString(X_DATE, y, GLUED_DATE_ROW)
        c.drawString(X_CREDIT, y, _money(GLUED_DATE_CREDIT))
        balance += GLUED_DATE_CREDIT
        y -= LINE_HEIGHT

    c.drawString(X_DATE, y, "31Oct")
    c.drawString(X_DESC, y, "CLOSING BALANCE")
    if split_closing_anchor:
        c.drawString(X_BALANCE, y + ANCHOR_SPLIT_OFFSET, _money(balance))
        c.drawString(X_BALANCE + 60, y, "CR")
    else:
        c.drawString(X_BALANCE, y, f"{_money(balance)}CR")

    c.save()
    return buf.getvalue()


def _expected_closing(glued_date_row=False):
    total = sum((c or 0) - (d or 0) for _, _, d, c in TRANSACTIONS)
    if glued_date_row:
        total += GLUED_DATE_CREDIT
    return OPENING + total


class SplitClosingAnchorTests(SimpleTestCase):
    """The defect: the closing figure rounds into a row of its own."""

    def setUp(self):
        self.pdf = build_pdf(split_closing_anchor=True)

    def test_the_keyword_row_really_is_left_without_an_amount(self):
        """Guard the premise: if row grouping ever stops splitting this row the
        test below would pass for the wrong reason."""
        with pdfplumber.open(io.BytesIO(self.pdf)) as pdf:
            rows = _rows(pdf)
        closing_rows = [
            r for r in rows
            if 'CLOSINGBALANCE' in ''.join(w['text'] for w in r)
        ]
        self.assertEqual(len(closing_rows), 1)
        texts = [w['text'] for w in closing_rows[0]]
        self.assertNotIn(_money(_expected_closing()), texts)

    def test_the_statement_is_not_rejected(self):
        result = parse_cba_geometry(self.pdf)
        self.assertEqual(len(result["transactions"]), len(TRANSACTIONS))

    def test_the_closing_balance_is_recovered(self):
        result = parse_cba_geometry(self.pdf)
        self.assertAlmostEqual(
            result["closing_balance"], _expected_closing(), places=2)

    def test_the_statement_reconciles(self):
        result = parse_cba_geometry(self.pdf)
        movements = sum(t["amount"] for t in result["transactions"])
        self.assertAlmostEqual(
            result["opening_balance"] + movements,
            result["closing_balance"],
            places=2,
        )


class IntactClosingAnchorTests(SimpleTestCase):
    """Recovery must not disturb a statement whose anchor row is intact."""

    def test_an_unsplit_statement_still_parses(self):
        result = parse_cba_geometry(build_pdf())
        self.assertEqual(len(result["transactions"]), len(TRANSACTIONS))
        self.assertAlmostEqual(result["opening_balance"], OPENING, places=2)
        self.assertAlmostEqual(
            result["closing_balance"], _expected_closing(), places=2)

    def test_a_statement_with_no_closing_anchor_is_still_rejected(self):
        """Recovery must not invent an anchor: a genuinely missing closing
        balance has to keep failing loudly rather than reconcile against
        whatever figure happens to sit nearby."""
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=A4, invariant=1)
        c.setFont("Helvetica", 9)
        c.drawString(X_DATE, 780, "Commonwealth Bank of Australia    Page 1 of 1")
        c.drawString(X_DATE, 744, "Date    Transaction    Debit    Credit    Balance")
        c.drawString(X_DATE, 726, "01Oct")
        c.drawString(X_DESC, 726, "2025 OPENING BALANCE")
        c.drawString(X_BALANCE, 726, f"{_money(OPENING)}CR")
        y = 708
        for date, desc, debit, credit in TRANSACTIONS:
            c.drawString(X_DATE, y, date)
            c.drawString(X_DESC, y, desc)
            c.drawString(X_DEBIT if debit else X_CREDIT, y,
                         _money(debit or credit))
            y -= LINE_HEIGHT
        c.save()
        with self.assertRaises(StatementParseError):
            parse_cba_geometry(buf.getvalue())


class DescriptionQualityTests(SimpleTestCase):
    """Page furniture must not become part of a transaction's description."""

    def test_stranded_header_pieces_stay_out_of_descriptions(self):
        result = parse_cba_geometry(build_pdf(page_furniture=True))
        descriptions = [t["description"] for t in result["transactions"]]
        self.assertEqual(len(descriptions), len(TRANSACTIONS))
        for text in descriptions:
            for stray in ("Statement", "YourStatement", "Your Statement",
                          "TransactionDebitCredit", "06269281523335"):
                self.assertNotIn(stray, text)

    def test_the_first_description_is_the_transactions_own(self):
        result = parse_cba_geometry(build_pdf(page_furniture=True))
        self.assertIn("EFTPOS SALES INV 1001",
                      result["transactions"][0]["description"])

    def test_a_date_glued_to_its_description_keeps_the_description(self):
        """The date cell is often set tight enough to arrive glued to the
        description. Discarding the whole word as "a date" threw away the
        transaction's only description -- 152 rows out of 240 on the real
        statement."""
        result = parse_cba_geometry(build_pdf(glued_date_row=True))
        glued = result["transactions"][-1]
        self.assertEqual(glued["date"], "2025-10-30")
        self.assertIn("DIRECTCREDITMCAREBENEFITS", glued["description"])
        self.assertNotIn("30Oct", glued["description"])

    def test_the_glued_row_still_reconciles(self):
        result = parse_cba_geometry(build_pdf(glued_date_row=True))
        movements = sum(t["amount"] for t in result["transactions"])
        self.assertAlmostEqual(
            result["opening_balance"] + movements,
            result["closing_balance"],
            places=2,
        )
