"""An asset bought part-way through the year earns part of a year's charge.

Found 2026-09-04 on Kinross Builders. A Range Rover bought 29 March 2024 was
charged a full year's 25% diminishing value in the year it was acquired --
17,027.00 on a 68,108.00 base -- where HandiLedger charged 4,373, being
94 days of ownership out of the 366 in FY2024.

Nothing in _calc_depreciation counted days. The only nod to a part year was the
General Pool's Div 328 convention, which substitutes a 15% half-year rate in the
acquisition year and deliberately does NOT count days -- so pool assets must be
left exactly as they are.

The denominator is the financial year's own length, not a flat 365. FY2024 was a
leap year, and 17,027 x 94/366 is the 4,373 that Kinross's trial balance actually
carries at account 2895. Using 365 gives 4,385 and the schedule stops agreeing
with the ledger it is meant to explain.

HandiLedger reports whole dollars; this system works in cents, so the figures
here are its 4,373 and 15,934 carried to 4,373.05 and 15,933.75. That is a
presentation convention, not a difference of method, and the expectations below
are written in cents because that is what the code returns.

Because the charge was never pro-rated, the closing written-down value it fed
into the next year was wrong too, which is how FY2025 came to open at cost.
"""
from datetime import date
from decimal import Decimal

from django.test import TestCase

from core.models import DepreciationAsset, Entity, FinancialYear
from core.views import _calc_depreciation


class AcquisitionYearIsProRatedTests(TestCase):
    def setUp(self):
        self.entity = Entity.objects.create(
            entity_name="Kinross Builders Pty Ltd", entity_type="company")
        # A leap financial year: 2024-06-30 less 2023-07-01 is 366 days.
        self.fy = FinancialYear.objects.create(
            entity=self.entity, year_label="2024",
            start_date=date(2023, 7, 1), end_date=date(2024, 6, 30))

    def _asset(self, **kwargs):
        defaults = dict(
            financial_year=self.fy,
            category="Motor Vehicles",
            asset_name="Range Rover AZJ923",
            total_cost=Decimal("68108.00"),
            opening_wdv=Decimal("68108.00"),
            method="D",
            rate=Decimal("25.00"),
        )
        defaults.update(kwargs)
        return DepreciationAsset(**defaults)

    def test_a_car_bought_in_march_earns_94_of_366_days(self):
        """The Kinross case: 68,108 x 25% x 94/366, HandiLedger's 4,373."""
        asset = self._asset(purchase_date=date(2024, 3, 29))

        _calc_depreciation(asset)

        self.assertEqual(asset.depreciation_amount, Decimal("4373.05"))
        self.assertEqual(asset.closing_wdv, Decimal("63734.95"))

    def test_the_denominator_is_the_years_own_length_not_a_flat_365(self):
        """The same 94 days in a non-leap year is a bigger share of it.

        Asserted against a different year rather than as "not 4,385" on the
        leap year, which would have passed before the feature existed and
        proved nothing.
        """
        fy23 = FinancialYear.objects.create(
            entity=self.entity, year_label="2023",
            start_date=date(2022, 7, 1), end_date=date(2023, 6, 30))
        asset = self._asset(
            financial_year=fy23, purchase_date=date(2023, 3, 29))

        _calc_depreciation(asset)

        # 17,027 x 94/365, against 4,373.05 for the same days over 366.
        self.assertEqual(asset.depreciation_amount, Decimal("4385.04"))

    def test_an_asset_held_all_year_is_charged_in_full(self):
        asset = self._asset(purchase_date=date(2020, 1, 1))

        _calc_depreciation(asset)

        self.assertEqual(asset.depreciation_amount, Decimal("17027.00"))

    def test_an_asset_with_no_purchase_date_is_charged_in_full(self):
        """101 of 119 live assets carry no purchase date; they must not move."""
        asset = self._asset(purchase_date=None)

        _calc_depreciation(asset)

        self.assertEqual(asset.depreciation_amount, Decimal("17027.00"))

    def test_an_asset_bought_on_the_last_day_earns_one_day(self):
        asset = self._asset(purchase_date=date(2024, 6, 30))

        _calc_depreciation(asset)

        # 17,027 x 1/366
        self.assertEqual(asset.depreciation_amount, Decimal("46.52"))

    def test_an_asset_bought_on_the_first_day_is_charged_in_full(self):
        asset = self._asset(purchase_date=date(2023, 7, 1))

        _calc_depreciation(asset)

        self.assertEqual(asset.depreciation_amount, Decimal("17027.00"))

    def test_prime_cost_is_pro_rated_the_same_way(self):
        asset = self._asset(method="P", purchase_date=date(2024, 3, 29))

        _calc_depreciation(asset)

        self.assertEqual(asset.depreciation_amount, Decimal("4373.05"))

    def test_a_general_pool_asset_keeps_its_div_328_half_year_rate(self):
        """Div 328 substitutes a 15% rate for day-counting. Pro-rating on top
        would halve it twice."""
        asset = self._asset(
            category="General Pool", purchase_date=date(2024, 3, 29),
            opening_wdv=Decimal("0.00"), addition_cost=Decimal("68108.00"))

        _calc_depreciation(asset, force_ato_rate=True)

        self.assertEqual(asset.rate, Decimal("15"))
        self.assertEqual(asset.depreciation_amount, Decimal("10216.20"))

    def test_a_written_off_asset_is_written_off_in_full(self):
        """Method W writes the whole value off; there is nothing to pro-rate."""
        asset = self._asset(method="W", purchase_date=date(2024, 3, 29))

        _calc_depreciation(asset)

        self.assertEqual(asset.depreciation_amount, Decimal("68108.00"))
        self.assertEqual(asset.closing_wdv, Decimal("0.00"))


class KinrossFY2025FollowsFromACorrectFY2024Tests(TestCase):
    """The year after: a correct closing value makes FY2025 agree with HandiLedger."""

    def test_the_year_after_opens_at_the_written_down_value(self):
        entity = Entity.objects.create(
            entity_name="Kinross Builders Pty Ltd", entity_type="company")
        fy25 = FinancialYear.objects.create(
            entity=entity, year_label="2025",
            start_date=date(2024, 7, 1), end_date=date(2025, 6, 30))
        asset = DepreciationAsset(
            financial_year=fy25, category="Motor Vehicles",
            asset_name="Range Rover AZJ923 - to dep limit",
            total_cost=Decimal("68108.00"),
            opening_wdv=Decimal("63735.00"),       # FY2024's closing
            purchase_date=date(2024, 3, 29),       # bought in the PRIOR year
            method="D", rate=Decimal("25.00"))

        _calc_depreciation(asset)

        # HandiLedger: 15,934
        self.assertEqual(asset.depreciation_amount, Decimal("15933.75"))
        self.assertEqual(asset.closing_wdv, Decimal("47801.25"))
