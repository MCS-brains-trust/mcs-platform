"""Depreciation must post to an expense account, and to its own asset's pair.

Found 2026-08-25 on Dr Services Family Trust FY2026. Posting depreciation on a
motor vehicle produced:

    Dr  1045  Depreciation - Plant             6,397.75
    Cr  2829  Less: Accumulated depreciation   6,397.75

Account 1045 sits in the ASSETS section of that chart, so the charge never
reached the profit and loss — profit was overstated by the full amount and the
balance sheet carried a debit that means nothing. Account 2829 is the
accumulated depreciation for Buildings; the vehicle's own accumulated account
is 2895, which already carried 12,763.00, so accumulated depreciation ended up
split across two unrelated accounts.

Both came from the fallback used when an asset is left on "Auto-detect". The
expense lookup took the first chart account whose name contains "depreciation"
with no filter on section, and 1045 sorts first. The accumulated lookup took
the first account whose name contains "accum" anywhere in the entity, with
nothing tying it to the asset being depreciated.

_find_paired_accum_dep already walks the asset accounts to find the accumulated
account sitting beneath a given cost account — 2890 pairs to 2895 — but it was
only consulted for assets created from a bank transaction.
"""
from datetime import date
from decimal import Decimal

from django.test import TestCase

from core.models import (
    Client as ClientModel,
    DepreciationAsset,
    Entity,
    EntityChartOfAccount,
    FinancialYear,
)


class DepreciationPostsToAnExpenseAccountTests(TestCase):
    def setUp(self):
        self.client_obj = ClientModel.objects.create(name="Resolver Client")
        self.entity = Entity.objects.create(
            entity_name="Resolving Pty Ltd", entity_type="company",
            client=self.client_obj)
        self.fy = FinancialYear.objects.create(
            entity=self.entity, year_label="2026",
            start_date=date(2025, 7, 1), end_date=date(2026, 6, 30),
            status=FinancialYear.Status.DRAFT)

    def _coa(self, code, name, section):
        return EntityChartOfAccount.objects.create(
            entity=self.entity, account_code=code, account_name=name,
            section=section, is_active=True)

    def _asset(self, **kw):
        defaults = dict(
            financial_year=self.fy, category="Motor Vehicles",
            asset_name="Vehicle", total_cost=Decimal("38354.00"),
            opening_wdv=Decimal("25591.00"), depreciation_amount=Decimal("6397.75"),
            private_depreciation=Decimal("0.00"),
            closing_wdv=Decimal("19193.25"), method="D", rate=Decimal("25.00"))
        defaults.update(kw)
        return DepreciationAsset.objects.create(**defaults)

    def _dr_services_chart(self):
        """The real chart: 1045 is an asset account despite its name."""
        self._coa("1045", "Depreciation - Plant", "assets")
        self._coa("1617", "Depreciation - Other", "expenses")
        self._coa("2815", "Buildings (cost)", "assets")
        self._coa("2829", "Less: Accumulated depreciation", "assets")
        self._coa("2890", "Motor vehicles (cost)", "assets")
        self._coa("2895", "Less: Accumulated depreciation", "assets")

    def _resolve(self, assets):
        from core.views import _resolve_depreciation_account_groups
        return _resolve_depreciation_account_groups(self.fy, assets)

    def test_the_expense_account_is_never_an_asset_account(self):
        """1045 is named 'Depreciation - Plant' but sits in assets."""
        self._dr_services_chart()

        groups, error = self._resolve([self._asset()])

        codes = {key[0] for key in groups}
        self.assertNotIn("1045", codes)

    def test_the_expense_account_comes_from_the_expenses_section(self):
        self._dr_services_chart()

        groups, error = self._resolve([self._asset()])

        self.assertEqual({key[0] for key in groups}, {"1617"})

    def test_no_expense_account_is_an_error_not_a_wrong_account(self):
        """Silently posting outside the P&L is worse than refusing."""
        self._coa("1045", "Depreciation - Plant", "assets")
        self._coa("2890", "Motor vehicles (cost)", "assets")
        self._coa("2895", "Less: Accumulated depreciation", "assets")

        groups, error = self._resolve([self._asset()])

        self.assertIsNotNone(error)
        self.assertIn("Depreciation Expense", error)

    # -- accumulated depreciation pairs to its own asset -------------------

    def test_the_accumulated_account_pairs_with_the_assets_cost_account(self):
        """2890 pairs to 2895, not to Buildings' 2829."""
        self._dr_services_chart()
        asset = self._asset(asset_account_code="2890",
                            asset_account_name="Motor vehicles (cost)")

        groups, error = self._resolve([asset])

        self.assertEqual({key[2] for key in groups}, {"2895"})

    def test_a_different_asset_pairs_to_its_own_accumulated_account(self):
        self._dr_services_chart()
        asset = self._asset(asset_name="Building",
                            asset_account_code="2815",
                            asset_account_name="Buildings (cost)")

        groups, error = self._resolve([asset])

        self.assertEqual({key[2] for key in groups}, {"2829"})

    def test_an_explicit_accumulated_code_on_the_asset_still_wins(self):
        self._dr_services_chart()
        asset = self._asset(asset_account_code="2890",
                            accum_dep_code="2899",
                            accum_dep_name="Less: Accumulated depreciation")

        groups, error = self._resolve([asset])

        self.assertEqual({key[2] for key in groups}, {"2899"})

    def test_an_explicit_expense_code_on_the_asset_still_wins(self):
        self._dr_services_chart()
        asset = self._asset(dep_expense_code="1803",
                            dep_expense_name="Depreciation - M/V car")

        groups, error = self._resolve([asset])

        self.assertEqual({key[0] for key in groups}, {"1803"})

    def test_the_dr_services_posting_would_now_be_correct(self):
        """Regression: the exact pair that produced the bad journal."""
        self._dr_services_chart()
        asset = self._asset(asset_account_code="2890",
                            asset_account_name="Motor vehicles (cost)")

        groups, error = self._resolve([asset])

        self.assertIsNone(error)
        (dep_code, _dep_name, accum_code, _accum_name), amount = next(iter(groups.items()))
        self.assertEqual((dep_code, accum_code), ("1617", "2895"))
        self.assertEqual(amount, Decimal("6397.75"))
