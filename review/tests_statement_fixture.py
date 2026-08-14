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
    """Two independent properties, not two halves of one mechanism:
    detect_bank routes this fixture to "cba" purely on the preamble's
    bank-name text ("Commonwealth Bank of Australia"). The header's
    real-whitespace spacing and the glued date literal are properties of
    the fixture's input shape to the geometry parser, not of bank
    detection -- test_the_header_keeps_its_spaces_but_the_dates_do_not
    below covers those, independently of routing."""

    def setUp(self):
        self.pdf = make_cba.build_pdf()

    def test_detect_bank_identifies_it_as_cba(self):
        """A guard, not a red test -- make_cba.py's preamble already carries
        "Commonwealth Bank of Australia", deliberately, per the generator's
        own docstring. This stayed green on the first run; it is kept to
        prove the fixture is routed to "cba" rather than falling through to
        "unknown" or a different bank's parser.

        The routing here is a bare substring match on the preamble text
        (review/pdf_parsers.py's `is_cba = "commonwealth bank" in text_lower
        or "commbank" in text_lower`), independent of the transaction
        table's column spacing -- detect_bank's only whitespace-sensitive
        regex distinguishes the CBA "Transaction Listing" (NetBank export)
        header shape from the standard one, a branch this fixture's header
        never reaches since it contains neither "details" nor "amount".
        So this test does not exercise the header-spacing half of the fixture's
        dual property; only test_the_header_keeps_its_spaces_but_the_dates_do_not
        does that."""
        from review.pdf_parsers import detect_bank

        self.assertEqual(detect_bank(self.pdf), "cba")

    def test_the_header_keeps_its_spaces_but_the_dates_do_not(self):
        """A guard, not a red test -- this checks two rendering properties
        make_cba.py's docstring documents as deliberate, neither of which is
        about bank detection (see the preceding test): the header is drawn
        with wide inter-word gaps ("Date    Transaction    ...") so
        extract_text() preserves real spaces there, while each date is
        stored and drawn as a single glued literal ("02Oct", one
        c.drawString call, no separate "02"/"Oct" word pair), so
        extract_text() reproduces it unglued only if someone edits the
        source string to add the space back in. Both were true before this
        test existed, so it passed on first run."""
        import io
        import pdfplumber

        with pdfplumber.open(io.BytesIO(self.pdf)) as pdf:
            text = pdf.pages[0].extract_text()
        self.assertRegex(text, r"Date\s+Transaction\s+Debit\s+Credit\s+Balance")
        self.assertIn("02Oct", text)
        self.assertNotIn("02 Oct", text)
