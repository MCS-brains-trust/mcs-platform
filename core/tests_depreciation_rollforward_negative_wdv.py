"""Roll-forward must not carry a negative written-down value.

Found 2026-08-25 on Dr Services Family Trust FY2026, where the vehicle showed
negative depreciation. The chain:

    FY2024  open       0.00  add 38,354.00  dep      0.00  close  38,354.00
    FY2025  open       0.00  add      0.00  dep 34,136.00  close -34,136.00
    FY2026  open -34,136.00  add      0.00  dep -8,534.00  close -25,602.00

FY2025's opening written-down value arrived as zero — the HandiLedger import
falls back to zero when it cannot find prior-year detail for an asset
(access_ledger_import.py) — so a full year of depreciation was charged against
nothing and the closing value went negative. Roll-forward then copied that
negative straight into FY2026 as the opening value, and applying 25%
diminishing value to a negative base produced negative depreciation, which
reads as income.

An asset cannot be worth less than nothing. The import path already clamps a
negative opening to zero; the two roll-forward paths did not, which is the
asymmetry that let this through. Both now refuse to carry a negative opening
and record what happened on the asset, because silently zeroing it is what
allowed seven years of this to go unnoticed on another entity.
"""
from datetime import date
from decimal import Decimal

from django.test import TestCase

from core.models import (
    Client as ClientModel,
    DepreciationAsset,
    Entity,
    FinancialYear,
)


class RollForwardRefusesNegativeOpeningTests(TestCase):
    def setUp(self):
        self.client_obj = ClientModel.objects.create(name="Rollforward Client")
        self.entity = Entity.objects.create(
            entity_name="Rolling Pty Ltd", entity_type="company",
            client=self.client_obj)
        self.fy = FinancialYear.objects.create(
            entity=self.entity, year_label="2025",
            start_date=date(2024, 7, 1), end_date=date(2025, 6, 30),
            status=FinancialYear.Status.FINALISED)

    def _asset(self, closing, name="Vehicle"):
        return DepreciationAsset.objects.create(
            financial_year=self.fy, category="Motor Vehicles",
            asset_name=name, total_cost=Decimal("38354.00"),
            opening_wdv=Decimal("0.00"), depreciation_amount=Decimal("34136.00"),
            closing_wdv=Decimal(closing), method="D", rate=Decimal("25.00"))

    def test_a_negative_closing_does_not_become_a_negative_opening(self):
        """The Dr Services defect: -34,136 carried straight into the new year."""
        from core.views import _rolled_forward_opening_wdv

        self.assertEqual(
            _rolled_forward_opening_wdv(self._asset("-34136.00")),
            Decimal("0.00"),
        )

    def test_a_positive_closing_is_carried_unchanged(self):
        """The normal path must be untouched."""
        from core.views import _rolled_forward_opening_wdv

        self.assertEqual(
            _rolled_forward_opening_wdv(self._asset("25591.00")),
            Decimal("25591.00"),
        )

    def test_a_nil_closing_is_carried_as_nil(self):
        from core.views import _rolled_forward_opening_wdv

        self.assertEqual(
            _rolled_forward_opening_wdv(self._asset("0.00")),
            Decimal("0.00"),
        )

    def test_the_clamp_is_recorded_on_the_asset_not_silent(self):
        """Silently zeroing is what let this run unnoticed for seven years."""
        from core.views import _rolled_forward_opening_wdv, _rollforward_wdv_note

        asset = self._asset("-34136.00")
        note = _rollforward_wdv_note(asset)

        self.assertIsNotNone(note)
        self.assertIn("-34136", note.replace(",", ""))

    def test_a_healthy_asset_gets_no_note(self):
        from core.views import _rollforward_wdv_note

        self.assertIsNone(_rollforward_wdv_note(self._asset("25591.00")))

    def test_negative_depreciation_can_never_be_produced_from_the_clamp(self):
        """A clamped opening must not then generate a negative charge."""
        from core.views import _calc_depreciation, _rolled_forward_opening_wdv

        prior = self._asset("-34136.00")
        rolled = DepreciationAsset(
            financial_year=self.fy, category=prior.category,
            asset_name=prior.asset_name, total_cost=prior.total_cost,
            opening_wdv=_rolled_forward_opening_wdv(prior),
            addition_cost=Decimal("0.00"),
            method=prior.method, rate=prior.rate)

        _calc_depreciation(rolled, force_ato_rate=False)

        self.assertGreaterEqual(rolled.depreciation_amount, Decimal("0"))
        self.assertGreaterEqual(rolled.closing_wdv, Decimal("0"))
