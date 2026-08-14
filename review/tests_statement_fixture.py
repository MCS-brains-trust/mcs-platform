"""The synthesised CBA fixture must satisfy the real geometry parser.

If this fails, no Playwright test built on the fixture can mean anything --
the statement would never get past parsing.
"""
from django.test import SimpleTestCase

from e2e.fixtures.statements import make_cba
from review.statement_geometry import parse_cba_geometry


class FixtureParsesTests(SimpleTestCase):
    def setUp(self):
        self.pdf = make_cba.build_pdf()
        self.result = parse_cba_geometry(self.pdf)

    def test_all_six_transactions_are_extracted(self):
        self.assertEqual(len(self.result["transactions"]), 6)

    def test_the_balances_anchor_the_statement(self):
        self.assertEqual(self.result["opening_balance"], 10000.00)
        self.assertEqual(self.result["closing_balance"], 13228.00)

    def test_debits_are_negative_and_credits_positive(self):
        by_desc = {t["description"]: t["amount"] for t in self.result["transactions"]}
        self.assertAlmostEqual(by_desc["EFTPOS SALES INV 1001"], 1100.00, places=2)
        self.assertAlmostEqual(by_desc["OFFICE SUPPLIES PTY LTD"], -550.00, places=2)
        self.assertAlmostEqual(by_desc["EXPORT SALE INV 1003"], 800.00, places=2)

    def test_the_dates_survive_the_kerning_collapse(self):
        """The defect this fixture reproduces: extract_text() drops the space in
        '31 Oct', so a date regex expecting a space matched zero lines and the
        parser returned nothing. The geometry engine reads the glued form."""
        dates = sorted(t["date"] for t in self.result["transactions"])
        self.assertEqual(dates[0], "2025-10-02")
        self.assertEqual(dates[-1], "2025-10-28")

    def test_the_statement_reconciles(self):
        total = sum(t["amount"] for t in self.result["transactions"])
        self.assertAlmostEqual(
            self.result["opening_balance"] + total,
            self.result["closing_balance"],
            places=2,
        )

    def test_the_pdf_regenerates_byte_for_byte(self):
        """A guard, not a red test -- invariant mode was already on when this
        was written, so it passed on the first run. It stays in place because
        the committed binary must be exactly what the script produces, or a
        reviewer cannot tell what they are approving, and reportlab embeds a
        /CreationDate unless invariant mode is on."""
        self.assertEqual(make_cba.build_pdf(), make_cba.build_pdf())


class FixtureRoutingTests(SimpleTestCase):
    """The dual property: a header with real whitespace so detect_bank fires,
    and glued dates so the geometry engine is the thing that parses it."""

    def setUp(self):
        self.pdf = make_cba.build_pdf()

    def test_detect_bank_identifies_it_as_cba(self):
        """A guard, not a red test -- make_cba.py's preamble already carries
        "Commonwealth Bank of Australia" and its header is drawn with real
        spaces, both deliberately, per the generator's own docstring. This
        stayed green on the first run; it is kept to prove the fixture is
        routed to the geometry parser rather than falling through to
        "unknown" or a different bank's parser."""
        from review.pdf_parsers import detect_bank

        self.assertEqual(detect_bank(self.pdf), "cba")

    def test_the_header_keeps_its_spaces_but_the_dates_do_not(self):
        """A guard, not a red test -- this is the dual property make_cba.py's
        docstring already documents as deliberate: the header is drawn with
        wide inter-word gaps ("Date    Transaction    ...") so detect_bank's
        whitespace regex matches extract_text(), while each date is drawn as
        a single close-set string ("02Oct") so pdfplumber's word-clustering
        glues it into one token instead of "02 Oct". Both were true before
        this test existed, so it passed on first run."""
        import io
        import pdfplumber

        with pdfplumber.open(io.BytesIO(self.pdf)) as pdf:
            text = pdf.pages[0].extract_text()
        self.assertRegex(text, r"Date\s+Transaction\s+Debit\s+Credit\s+Balance")
        self.assertIn("02Oct", text)
        self.assertNotIn("02 Oct", text)
