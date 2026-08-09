"""
Regression tests for balance-sheet classification during roll forward.

_is_balance_sheet_account decides which accounts carry their closing balance into
next year as an opening balance and which reset to zero. Its callers built the
coa_sections lookup exclusively from the entity-TYPE ChartOfAccount template and
never consulted the entity's own EntityChartOfAccount, so an entity whose chart is
not modelled by the type template fell through to the numeric fallback --
`code_prefix.isdigit() and int(code_prefix) >= 2000` -- which returns False for any
code that is not purely numeric.

False means P&L, so every account on such a chart was rolled forward with a zeroed
opening balance instead of its prior closing balance, and the reroll diff that is
supposed to catch exactly that was blind for the same reason.

MYOB/AccountRight-style hyphenated codes ("1-1000") are the common shape that
triggers it.
"""

from decimal import Decimal

from django.test import TestCase, override_settings

from accounts.models import User
from core.models import (
    ChartOfAccount,
    Client as ClientModel,
    Entity,
    EntityChartOfAccount,
)
from core.views import _coa_sections_for_entity, _is_balance_sheet_account

STORAGES_OVERRIDE = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

# The shape core/e2e_fixture_data.py seeds, and what a MYOB export looks like.
HYPHENATED_CHART = [
    ("1-1000", "Cash at Bank", "current_assets", True),
    ("1-2000", "Plant and Equipment", "non_current_assets", True),
    ("1-2100", "Accumulated Depreciation", "non_current_assets", True),
    ("2-1000", "Trade Creditors", "current_liabilities", True),
    ("3-1000", "Retained Earnings", "equity", True),
    ("4-1000", "Sales", "revenue", False),
    ("6-1000", "Administration", "expenses", False),
]


@override_settings(STORAGES=STORAGES_OVERRIDE)
class RollForwardClassificationTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="rollfwd_cls_admin",
            password="testpass123",
            role=User.Role.ADMIN,
            totp_secret="dummy-secret-rollfwd-cls",
            totp_confirmed=True,
        )
        cls.client_obj = ClientModel.objects.create(name="Roll Forward Test Client")
        cls.entity = Entity.objects.create(
            entity_name="Roll Forward Test Co Pty Ltd",
            entity_type="company",
            client=cls.client_obj,
            primary_accountant=cls.user,
        )
        for code, name, section, _is_bs in HYPHENATED_CHART:
            EntityChartOfAccount.objects.create(
                entity=cls.entity,
                account_code=code,
                account_name=name,
                section=section,
                is_active=True,
            )

    def test_the_entitys_own_chart_supplies_the_sections(self):
        """
        The type template models none of these codes, so the entity's own chart is
        the only place the classification exists.
        """
        sections = _coa_sections_for_entity(self.entity)

        self.assertEqual(sections["1-1000"], "current_assets")
        self.assertEqual(sections["3-1000"], "equity")
        self.assertEqual(sections["4-1000"], "revenue")

    def test_balance_sheet_accounts_on_a_hyphenated_chart_are_carried_forward(self):
        """Assets, liabilities and equity must classify as balance sheet."""
        sections = _coa_sections_for_entity(self.entity)

        for code, name, _section, expected_bs in HYPHENATED_CHART:
            self.assertEqual(
                _is_balance_sheet_account(code, None, sections),
                expected_bs,
                f"{code} {name} classified wrongly",
            )

    def test_the_entitys_own_chart_wins_over_the_type_template(self):
        """
        A code the type template also defines takes the entity's classification —
        the entity's chart is the one its trial balance is actually keyed to.
        """
        ChartOfAccount.objects.create(
            entity_type="company",
            account_code="1-1000",
            account_name="Something Else",
            section="revenue",
            is_active=True,
        )

        sections = _coa_sections_for_entity(self.entity)

        self.assertEqual(sections["1-1000"], "current_assets")
        self.assertTrue(_is_balance_sheet_account("1-1000", None, sections))

    def test_the_type_template_still_supplies_codes_the_entity_lacks(self):
        """Entity charts are layered over the template, not swapped for it."""
        ChartOfAccount.objects.create(
            entity_type="company",
            account_code="9-9000",
            account_name="Template Only Account",
            section="current_assets",
            is_active=True,
        )

        sections = _coa_sections_for_entity(self.entity)

        self.assertEqual(sections["9-9000"], "current_assets")
        self.assertTrue(_is_balance_sheet_account("9-9000", None, sections))

    def test_rolling_forward_a_hyphenated_chart_carries_balance_sheet_openings(self):
        """
        End to end over _populate_rolled_forward_fy, on the fixture's own figures.

        Five balance-sheet accounts carry their closing balance forward as an
        opening; the two P&L accounts carry a comparative with a zero opening; and
        because this chart has no 4199 account to hold it, the $20,000 net profit
        (Sales 30,000 − Administration 10,000) is closed into a synthesised retained
        profits line. Eight rows, and they balance.

        Before the fix every one of these accounts classified as P&L, so nothing
        carried an opening balance at all.
        """
        from datetime import date

        from core.models import FinancialYear, TrialBalanceLine
        from core.views import _populate_rolled_forward_fy

        prior_fy = FinancialYear.objects.create(
            entity=self.entity,
            year_label="FY2025",
            start_date=date(2024, 7, 1),
            end_date=date(2025, 6, 30),
            status=FinancialYear.Status.FINALISED,
        )
        new_fy = FinancialYear.objects.create(
            entity=self.entity,
            year_label="FY2026",
            start_date=date(2025, 7, 1),
            end_date=date(2026, 6, 30),
            status=FinancialYear.Status.DRAFT,
        )
        for code, name, closing in [
            ("1-1000", "Cash at Bank", Decimal("70000.00")),
            ("1-2000", "Plant and Equipment", Decimal("20000.00")),
            ("1-2100", "Accumulated Depreciation", Decimal("-4000.00")),
            ("2-1000", "Trade Creditors", Decimal("-6000.00")),
            ("3-1000", "Retained Earnings", Decimal("-60000.00")),
            ("4-1000", "Sales", Decimal("-30000.00")),
            ("6-1000", "Administration", Decimal("10000.00")),
        ]:
            TrialBalanceLine.objects.create(
                financial_year=prior_fy,
                account_code=code,
                account_name=name,
                closing_balance=closing,
                debit=closing if closing > 0 else Decimal("0"),
                credit=-closing if closing < 0 else Decimal("0"),
                source="tb_import",
            )

        _populate_rolled_forward_fy(prior_fy, new_fy)

        rolled = {
            line.account_code: line
            for line in TrialBalanceLine.objects.filter(financial_year=new_fy)
        }

        # Balance-sheet accounts carry their closing balance as next year's opening.
        for code, expected_opening in [
            ("1-1000", Decimal("70000.00")),
            ("1-2000", Decimal("20000.00")),
            ("1-2100", Decimal("-4000.00")),
            ("2-1000", Decimal("-6000.00")),
            ("3-1000", Decimal("-60000.00")),
        ]:
            self.assertIn(code, rolled, f"{code} did not roll forward at all")
            self.assertEqual(
                rolled[code].opening_balance, expected_opening,
                f"{code} carried the wrong opening balance",
            )

        # P&L accounts appear as a comparative only — never as an opening balance.
        for code in ("4-1000", "6-1000"):
            self.assertIn(code, rolled)
            self.assertEqual(rolled[code].opening_balance, Decimal("0"))

        # No 4199 exists on this chart, so the net P&L is closed into a new one.
        self.assertIn("4199", rolled, "the net profit was not closed anywhere")
        self.assertEqual(rolled["4199"].opening_balance, Decimal("-20000.00"))

        self.assertEqual(len(rolled), 8)
        self.assertEqual(
            sum(line.opening_balance for line in rolled.values()),
            Decimal("0.00"),
            "the rolled-forward opening balances must balance",
        )

    def test_inactive_entity_accounts_are_ignored(self):
        """Matches the is_active filter the template lookup already applied."""
        EntityChartOfAccount.objects.create(
            entity=self.entity,
            account_code="5-5000",
            account_name="Retired Account",
            section="current_assets",
            is_active=False,
        )

        self.assertNotIn("5-5000", _coa_sections_for_entity(self.entity))
