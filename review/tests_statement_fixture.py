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
