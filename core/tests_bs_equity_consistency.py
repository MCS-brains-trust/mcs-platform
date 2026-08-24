"""Total equity must carry the current year's result.

StatementHub keeps an UNCLOSED trial balance: the year's profit stays in the
P&L accounts and is only transferred into retained earnings at roll-forward.
fs_template_service knows this and injects a "Current year profit / (loss)"
line into equity before rendering (see the roll-forward block around
fs_template_service.py:1354), which is why the generated PDF balance sheet
reconciles.

document_context_builder never learned the same rule. It took total_equity as
the raw sum of the equity trial-balance lines, so for every company year
total_assets - total_liabilities - total_equity came out at exactly the
year's profit and it logged "Balance sheet imbalance of ...". Found on DJLH
Properties FY2025: a 113,621.77 "imbalance" reported against a balance sheet
that was in fact correct.

Two costs. The alarm cried on every company year, so a real imbalance would
have looked exactly like the noise. And total_equity is not only used for the
check -- debt_to_equity_ratio divides by it, so the ratio was wrong by the
profit as well.

The fix must not simply silence the check: a trial balance that genuinely
does not balance still has to be reported.
"""
from decimal import Decimal

from django.test import TestCase

from core.document_context_builder import DocumentContextBuilder
from core.models import FirmSettings
from core.tests_fs_company_generation import build_company_fy

# (account_code, name, standard_code, current, prior)
# Balanced, unclosed: profit of 300 sits in the P&L, retained earnings still
# carries its opening 200.
BALANCED = [
    ("2000", "Cash at bank",       "BS-CA-001", Decimal("1000"), Decimal("0")),
    ("3048", "Trade creditors",    "BS-CL-001", Decimal("-400"), Decimal("0")),
    ("4200", "Issued capital",     "BS-EQ-001", Decimal("-100"), Decimal("0")),
    ("4199", "Retained profits",   "BS-EQ-002", Decimal("-200"), Decimal("0")),
    ("620",  "Rents received",     "IS-REV-001", Decimal("-500"), Decimal("0")),
    ("1510", "Accountancy",        "IS-EXP-001", Decimal("200"),  Decimal("0")),
]

# The same set with 100 of assets that nothing accounts for: the trial balance
# itself is out, and no amount of profit injection can close it.
UNBALANCED = [
    ("2000", "Cash at bank",       "BS-CA-001", Decimal("1100"), Decimal("0")),
    ("3048", "Trade creditors",    "BS-CL-001", Decimal("-400"), Decimal("0")),
    ("4200", "Issued capital",     "BS-EQ-001", Decimal("-100"), Decimal("0")),
    ("4199", "Retained profits",   "BS-EQ-002", Decimal("-200"), Decimal("0")),
    ("620",  "Rents received",     "IS-REV-001", Decimal("-500"), Decimal("0")),
    ("1510", "Accountancy",        "IS-EXP-001", Decimal("200"),  Decimal("0")),
]

LOGGER = "core.document_context_builder"


class TotalEquityCarriesTheYearsResultTests(TestCase):
    def setUp(self):
        # The builder refuses to produce financial statements without these
        # (APES 305), so every test needs a firm on file.
        firm = FirmSettings.objects.first() or FirmSettings()
        firm.firm_name = "MC & S Pty Ltd"
        firm.tax_agent_number = "12345678"
        firm.save()

    def _build(self, rows, name):
        fy = build_company_fy(rows, with_prior=False, entity_name=name)
        return DocumentContextBuilder(
            fy.entity, financial_year=fy).build("financial_statements")

    def test_the_balance_sheet_reconciles(self):
        ctx = self._build(BALANCED, "Equity Consistency Co")
        diff = (ctx["total_assets"] - ctx["total_liabilities"]
                - ctx["total_equity"])
        self.assertEqual(diff, Decimal("0"))

    def test_total_equity_equals_net_assets(self):
        ctx = self._build(BALANCED, "Equity Equals NA Co")
        self.assertEqual(ctx["total_equity"], ctx["net_assets"])

    def test_total_equity_includes_the_profit_not_just_the_equity_accounts(self):
        """Issued capital 100 + retained 200 = 300 on the accounts; the year
        made 300, so closing equity is 600."""
        ctx = self._build(BALANCED, "Equity Includes Profit Co")
        self.assertEqual(ctx["net_profit"], Decimal("300"))
        self.assertEqual(ctx["total_equity"], Decimal("600"))

    def test_a_correct_balance_sheet_raises_no_warning(self):
        """The alarm cried on every company year, which is the same as no
        alarm at all."""
        with self.assertNoLogs(LOGGER, level="WARNING"):
            self._build(BALANCED, "Quiet Alarm Co")

    def test_a_genuinely_unbalanced_trial_balance_still_warns(self):
        """The fix must not be a mute button."""
        with self.assertLogs(LOGGER, level="WARNING") as captured:
            self._build(UNBALANCED, "Real Imbalance Co")
        self.assertTrue(
            any("imbalance" in m.lower() for m in captured.output),
            captured.output)
