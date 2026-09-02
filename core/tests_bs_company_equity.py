"""A company presents closing retained profits as one line, per HandiLedger.

DJLH Properties Pty Ltd FY2024, HandiLedger::

    Equity
      Issued Capital
        Issued & paid up capital                        12        12
      Retained profits / (accumulated losses)      193,542   291,274
    Total Equity                                   193,554   291,286

StatementHub presented the *movement* instead -- opening retained profits, the
current year result, and dividends as three separate face lines::

    Retained profits                            291,274  (296,693)
    Issued & paid up capital                         12        12
    Current year profit / (loss)                (97,732)  587,966
    Total Equity                                193,554   291,286

Both foot to the same total; the difference is presentation. Trusts already
collapse to a single cumulative line via ``_collapse_trust_equity_to_accumulated``
-- this is the company equivalent, and the two are deliberately separate because
a trust's line is "Accumulated losses" and carries different rules.
"""
from decimal import Decimal

from django.test import SimpleTestCase

from core.fs_template_service import (
    _collapsed_company_equity,
    _format_lines,
    _safe_amp,
    _with_issued_capital_heading,
)

D = Decimal


def _row(name, cy, py, code="", standard_code=None):
    return {"account_code": code, "account_name": name,
            "cy_amount": D(cy), "py_amount": D(py),
            "standard_code": standard_code}


def _names(sections):
    return [r["account_name"] for r in sections["equity"]]


class CompanyEquityCollapseTests(SimpleTestCase):
    def setUp(self):
        # Raw TB values: equity is credit-normal, so a credit is negative.
        # DJLH FY2024: retained b/f (291,274 cr) and the year's loss (97,732 dr).
        self.sections = {"equity": [
            _row("Retained profits", "-291273.76", "296693.00", "4199"),
            _row("Issued & paid up capital", "-12.00", "-12.00", "4200",
                 standard_code="BS-EQ-001"),
            _row("Current year profit / (loss)", "97731.92", "-587966.00",
                 "NET_PROFIT"),
        ]}
        self.sections["equity"] = _collapsed_company_equity(
            self.sections["equity"])

    def test_capital_comes_first(self):
        self.assertEqual(_names(self.sections)[0], "Issued & paid up capital")

    def test_the_movement_lines_are_gone(self):
        names = _names(self.sections)
        self.assertNotIn("Current year profit / (loss)", names)
        self.assertEqual(
            len(names), 2,
            f"expected capital plus one retained line, got {names}",
        )

    def test_retained_profits_is_the_closing_balance(self):
        """291,274 brought forward less the 97,732 loss = 193,542."""
        retained = [r for r in self.sections["equity"]
                    if r["account_name"].startswith("Retained profits")][0]
        self.assertEqual(retained["cy_amount"], D("-193541.84"))
        self.assertEqual(retained["py_amount"], D("-291273.00"))

    def test_the_line_is_named_as_handiledger_names_it(self):
        self.assertEqual(
            _names(self.sections)[1],
            "Retained profits / (accumulated losses)",
        )

    def test_total_equity_is_unchanged(self):
        total = sum(r["cy_amount"] for r in self.sections["equity"])
        self.assertEqual(
            total, D("-193553.84"),
            "collapsing must not change what equity totals",
        )


class DividendsAreAbsorbedTests(SimpleTestCase):
    """DJLH FY2025 declared 609,453 of dividends."""

    def test_dividends_fold_into_retained_profits(self):
        sections = {"equity": [
            _row("Dividends provided for or paid", "609453.00", "0", "4160"),
            _row("Retained profits", "-193541.84", "-291273.76", "4199"),
            _row("Issued & paid up capital", "-12.00", "-12.00", "4200",
                 standard_code="BS-EQ-001"),
            _row("Current year profit / (loss)", "-109649.00", "97731.92",
                 "NET_PROFIT"),
        ]}
        before = sum(r["cy_amount"] for r in sections["equity"])
        sections["equity"] = _collapsed_company_equity(sections["equity"])

        names = [r["account_name"] for r in sections["equity"]]
        self.assertNotIn("Dividends provided for or paid", names)
        self.assertEqual(len(names), 2)
        self.assertEqual(
            sum(r["cy_amount"] for r in sections["equity"]), before,
            "total equity moved",
        )
        retained = sections["equity"][1]
        # 193,541.84 cr + 109,649 cr - 609,453 dr = 306,262.16 dr
        self.assertEqual(retained["cy_amount"], D("306262.16"))


class EdgeCaseTests(SimpleTestCase):
    def test_equity_with_only_capital_is_left_alone(self):
        sections = {"equity": [
            _row("Issued & paid up capital", "-12.00", "-12.00", "4200",
                 standard_code="BS-EQ-001"),
        ]}
        sections["equity"] = _collapsed_company_equity(sections["equity"])
        self.assertEqual(len(sections["equity"]), 1)
        self.assertEqual(sections["equity"][0]["account_name"],
                         "Issued & paid up capital")

    def test_an_empty_equity_section_is_safe(self):
        sections = {"equity": []}
        sections["equity"] = _collapsed_company_equity(sections["equity"])
        self.assertEqual(sections["equity"], [])


class IssuedCapitalHeadingTests(SimpleTestCase):
    """A company's equity carries HandiLedger's "Issued Capital" sub-heading.

    DJLH Properties Pty Ltd FY2024 prints it in the same style as every other
    balance-sheet sub-group heading, above both equity lines, with no subtotal
    of its own -- Total Equity closes the section::

        Equity
        Issued Capital
        Issued & paid up capital                        12        12
        Retained profits / (accumulated losses)    193,542   291,274
        Total Equity                               193,554   291,286

    StatementHub printed the two lines with no heading at all. "Issued
    Capital" is company wording, so sole traders and partnerships are left
    exactly as they are -- HandiLedger offers no exemplar for those, and
    inventing one is not this change's job.
    """

    def _rows(self):
        return _format_lines([
            _row("Issued & paid up capital", "-12.00", "-12.00", "4200",
                 standard_code="BS-EQ-001"),
            _row("Retained profits / (accumulated losses)",
                 "-193541.84", "-291273.76", "RETAINED"),
        ], credit_normal=True)

    def test_a_company_gets_the_heading_above_its_capital_line(self):
        rows = _with_issued_capital_heading("company", self._rows())
        self.assertTrue(rows[0].get("is_heading"))
        self.assertEqual(rows[0]["account_name"], "Issued Capital")
        # _format_lines has already escaped the ampersand for docxtpl.
        self.assertEqual(rows[1]["account_name"],
                         _safe_amp("Issued & paid up capital"))

    def test_the_heading_carries_no_amounts(self):
        rows = _with_issued_capital_heading("company", self._rows())
        self.assertEqual(rows[0]["cy_formatted"], "")
        self.assertEqual(rows[0]["py_formatted"], "")

    def test_the_heading_is_not_a_nested_tier(self):
        """It is a group heading, styled like "Payables", not like "Secured:"."""
        rows = _with_issued_capital_heading("company", self._rows())
        self.assertFalse(rows[0].get("is_nested"))

    def test_no_subtotal_is_added(self):
        rows = _with_issued_capital_heading("company", self._rows())
        self.assertEqual([r for r in rows if r.get("is_subtotal")], [])

    def test_both_equity_lines_survive(self):
        rows = _with_issued_capital_heading("company", self._rows())
        names = [r["account_name"] for r in rows if not r.get("is_heading")]
        self.assertEqual(names, [
            _safe_amp("Issued & paid up capital"),
            "Retained profits / (accumulated losses)",
        ])

    def test_a_sole_trader_is_left_alone(self):
        rows = self._rows()
        self.assertEqual(_with_issued_capital_heading("sole_trader", rows), rows)

    def test_a_partnership_is_left_alone(self):
        rows = self._rows()
        self.assertEqual(_with_issued_capital_heading("partnership", rows), rows)

    def test_a_trust_is_left_alone(self):
        rows = self._rows()
        self.assertEqual(_with_issued_capital_heading("trust", rows), rows)

    def test_an_empty_equity_section_gets_no_heading(self):
        """A heading standing over nothing would read as a missing figure."""
        self.assertEqual(_with_issued_capital_heading("company", []), [])

    def test_no_heading_when_there_is_no_contributed_capital(self):
        """Berwick Mechanical Services FY2021 has no issued capital account.

        Its equity is a single retained-profits line, so an "Issued Capital"
        heading would stand over a section that contains none -- it reads as a
        capital figure that failed to print.
        """
        rows = _format_lines([
            _row("Retained profits / (accumulated losses)",
                 "-181470.63", "-168337.00", "RETAINED"),
        ], credit_normal=True)
        self.assertEqual(_with_issued_capital_heading("company", rows), rows)

    def test_the_heading_survives_when_capital_is_not_the_first_row(self):
        """The gate is "is there capital", not "is row 0 capital"."""
        rows = _format_lines([
            _row("Retained profits / (accumulated losses)",
                 "-193541.84", "-291273.76", "RETAINED"),
            _row("Issued & paid up capital", "-12.00", "-12.00", "4200",
                 standard_code="BS-EQ-001"),
        ], credit_normal=True)
        out = _with_issued_capital_heading("company", rows)
        self.assertEqual(out[0]["account_name"], "Issued Capital")
