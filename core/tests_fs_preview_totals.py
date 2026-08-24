"""The on-screen statements preview must agree with the generated PDF.

The preview at /years/<pk>/statements/ builds its own aggregation straight
from TrialBalanceLine grouped by standard_code. It never touches
fs_template_service, so it missed the one thing that service does for an
unclosed trial balance: inject a "Current year profit / (loss)" line into
equity. The rendered PDF balance sheet therefore reconciled while the preview
of the same year did not, and the preview carried no subtotals at all — no
Total Assets, no Net Assets, no Total Equity, no Net Profit — so nothing on
the page revealed the gap.

Found on DJLH Properties FY2025: the PDF showed Net Assets and Total Equity
of -302,277.39; the preview showed neither, and its balance sheet rows summed
to the year's profit instead of nil.
"""
from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import User
from core.models import FirmSettings
from core.tests_fs_company_generation import (
    STORAGES_OVERRIDE,
    build_company_fy,
)

PROFIT_LABEL = "Current year profit / (loss)"

# Unclosed and balanced: 300 of profit still sitting in the P&L accounts.
UNCLOSED = [
    ("2000", "Cash at bank",     "BS-CA-001",  Decimal("1000"), Decimal("0")),
    ("3048", "Trade creditors",  "BS-CL-001",  Decimal("-400"), Decimal("0")),
    ("4200", "Issued capital",   "BS-EQ-001",  Decimal("-100"), Decimal("0")),
    ("4199", "Retained profits", "BS-EQ-002",  Decimal("-200"), Decimal("0")),
    ("620",  "Rents received",   "IS-REV-001", Decimal("-500"), Decimal("0")),
    ("1510", "Accountancy",      "IS-EXP-001", Decimal("200"),  Decimal("0")),
]

# No P&L activity at all, so the balance sheet already stands on its own.
NO_RESULT = [
    ("2000", "Cash at bank",     "BS-CA-001", Decimal("300"),  Decimal("0")),
    ("4200", "Issued capital",   "BS-EQ-001", Decimal("-100"), Decimal("0")),
    ("4199", "Retained profits", "BS-EQ-002", Decimal("-200"), Decimal("0")),
]


@override_settings(STORAGES=STORAGES_OVERRIDE)
class PreviewMatchesTheGeneratedStatementsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="fs_preview_admin", password="testpass123",
            role=User.Role.ADMIN,
            totp_secret="dummy-secret-fs-preview", totp_confirmed=True,
        )

    def setUp(self):
        firm = FirmSettings.objects.first() or FirmSettings()
        firm.firm_name = "MC & S Pty Ltd"
        firm.tax_agent_number = "12345678"
        firm.save()
        self.client.force_login(self.user)
        s = self.client.session
        s["2fa_verified"] = True
        s.save()

    def _context(self, rows, name):
        fy = build_company_fy(rows, with_prior=False, entity_name=name)
        url = reverse("core:financial_statements_view", kwargs={"pk": fy.pk})
        response = self.client.get(url, secure=True)
        self.assertEqual(response.status_code, 200)
        return response.context

    def test_the_profit_line_is_added_to_equity(self):
        ctx = self._context(UNCLOSED, "Preview Profit Line Co")
        labels = [e["label"] for e in ctx["balance_sheet"]]
        self.assertIn(PROFIT_LABEL, labels)

    def test_the_profit_line_carries_the_years_result(self):
        """Credit-normal, so a 300 profit shows as -300 alongside the other
        equity accounts."""
        ctx = self._context(UNCLOSED, "Preview Profit Amount Co")
        entry = next(e for e in ctx["balance_sheet"] if e["label"] == PROFIT_LABEL)
        self.assertEqual(entry["current"], Decimal("-300"))
        self.assertEqual(entry["section"], "Equity")

    def test_the_balance_sheet_now_sums_to_nil(self):
        ctx = self._context(UNCLOSED, "Preview Sums Nil Co")
        self.assertEqual(
            sum(e["current"] for e in ctx["balance_sheet"]), Decimal("0"))

    def test_no_profit_line_when_there_is_no_result(self):
        """A balance sheet that already stands must not gain an empty row."""
        ctx = self._context(NO_RESULT, "Preview No Result Co")
        labels = [e["label"] for e in ctx["balance_sheet"]]
        self.assertNotIn(PROFIT_LABEL, labels)

    def test_the_page_reports_the_headline_totals(self):
        ctx = self._context(UNCLOSED, "Preview Totals Co")
        self.assertEqual(ctx["net_profit"], Decimal("300"))
        self.assertEqual(ctx["total_assets"], Decimal("1000"))
        # Raw credit-normal, the convention every row on the page uses.
        self.assertEqual(ctx["total_liabilities"], Decimal("-400"))
        self.assertEqual(ctx["net_assets"], Decimal("600"))
        self.assertEqual(ctx["total_equity"], Decimal("-600"))

    def test_net_assets_and_total_equity_are_mirror_images(self):
        """The balance identity in this convention. If they stop summing to
        nil the balance sheet is out."""
        ctx = self._context(UNCLOSED, "Preview Mirror Co")
        self.assertEqual(ctx["net_assets"] + ctx["total_equity"], Decimal("0"))

    def test_each_section_carries_its_own_subtotal(self):
        ctx = self._context(UNCLOSED, "Preview Subtotals Co")
        equity = next(s for s in ctx["bs_sections"] if s["name"] == "Equity")
        # 100 capital + 200 retained + 300 result, credit-normal
        self.assertEqual(equity["total"], Decimal("-600"))
        revenue = next(s for s in ctx["is_sections"] if s["name"] == "Revenue")
        self.assertEqual(revenue["total"], Decimal("-500"))
