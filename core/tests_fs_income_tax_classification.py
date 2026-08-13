"""Which equity-section accounts are income tax, for financial statement generation.

build_company_context reclassifies income tax out of the equity section so the P&L
can show "Income tax attributable to operating profit (loss)" and Total Equity still
reconciles to Net Assets. Getting that wrong is unusually dangerous because the error
hides itself: an account moved out of equity and into tax still lands back in equity
via the profit line, so the balance sheet balances and the statements look right.

The rule was `4100 <= code_num <= 4149` on the account code alone. That is the
HandiLedger income-tax range for companies, but it is not exclusively tax:

  * 4115 is "Other income" on all six company charts in the production copy, and
    Berwick Mechanical Services FY2017 carries a real -62,189.73 balance on it named
    "Profit - Extraordinary". Extraordinary profit was being reported on the income
    tax line and added to profit.
  * Every account coded 4000-4999 is placed in sections["equity"] regardless of its
    own section, so pl_appropriation accounts reach this test too.
  * The rule also overrode the mapping the accountant sets during import, which the
    import gate refuses to let them skip.

The rule now trusts that mapping first (BS-EQ-011 Income tax provision), falls back
to the account name, and only then to the legacy code range.
"""
from decimal import Decimal

from django.test import TestCase

from core.fs_template_service import _is_income_tax_appropriation


def item(code, name, standard_code=None):
    return {
        "account_code": code,
        "account_name": name,
        "standard_code": standard_code,
        "cy_amount": Decimal("0"),
        "py_amount": Decimal("0"),
    }


class IncomeTaxClassificationTests(TestCase):
    # ── Explicit mapping wins ────────────────────────────────────────────────
    def test_an_account_mapped_to_the_income_tax_provision_is_tax(self):
        self.assertTrue(
            _is_income_tax_appropriation(item("4110", "Income tax on profit", "BS-EQ-011"))
        )

    def test_a_mapped_income_tax_account_outside_the_code_range_is_still_tax(self):
        self.assertTrue(
            _is_income_tax_appropriation(item("4900", "Taxation", "BS-EQ-011"))
        )

    def test_issued_capital_in_the_tax_code_range_is_not_tax(self):
        """The defect this test exists for.

        An accountant who maps 4100 to Issued capital during import had that
        decision overridden by the code range: the account vanished from equity and
        its balance was added to profit on the income tax line.
        """
        self.assertFalse(
            _is_income_tax_appropriation(item("4100", "Issued capital", "BS-EQ-001"))
        )

    def test_retained_earnings_in_the_tax_code_range_is_not_tax(self):
        self.assertFalse(
            _is_income_tax_appropriation(item("4120", "Retained profits", "BS-EQ-002"))
        )

    # ── Unmapped: fall back to the name ──────────────────────────────────────
    def test_extraordinary_profit_in_the_tax_code_range_is_not_tax(self):
        """Berwick Mechanical Services FY2017, account 4115, -62,189.73."""
        self.assertFalse(
            _is_income_tax_appropriation(item("4115", "Profit - Extraordinary"))
        )

    def test_other_income_in_the_tax_code_range_is_not_tax(self):
        self.assertFalse(_is_income_tax_appropriation(item("4115", "Other income")))

    def test_an_unmapped_income_tax_account_is_still_tax_by_name(self):
        for name in (
            "Income tax expense/income",
            "Income tax on profit",
            "Tax on profit",
            "Income tax expense",
        ):
            with self.subTest(name=name):
                self.assertTrue(_is_income_tax_appropriation(item("4110", name)))

    def test_over_and_under_provision_of_tax_are_tax(self):
        """4135 Over-provision of tax is on all six company charts, and its name
        carries no 'income tax' keyword -- it has to be matched explicitly."""
        for name in (
            "Over-provision of tax",
            "Under-provision of tax",
            "Over provision for tax",
            "Provision for income tax",
        ):
            with self.subTest(name=name):
                self.assertTrue(_is_income_tax_appropriation(item("4135", name)))

    # ── Legacy fallback, for a row carrying neither signal ───────────────────
    def test_an_unmapped_unnamed_account_in_the_range_stays_tax(self):
        """Preserves the old behaviour for a row that gives no other evidence,
        so an oddly-named tax account on an unmapped legacy year is not silently
        dropped out of the tax line."""
        self.assertTrue(_is_income_tax_appropriation(item("4110", "")))

    def test_an_account_outside_the_range_with_no_signal_is_not_tax(self):
        self.assertFalse(_is_income_tax_appropriation(item("4200", "Issued & paid up capital")))

    def test_a_non_numeric_code_is_handled(self):
        self.assertFalse(_is_income_tax_appropriation(item("3-1000", "Retained Earnings")))
        self.assertTrue(_is_income_tax_appropriation(item("3-1000", "Income tax expense")))
