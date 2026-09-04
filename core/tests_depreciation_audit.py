"""Reconciling a depreciation schedule against the trial balance.

Twenty-four assets carry a negative closing written-down value, which cannot be
true — Dr Services Family Trust's vehicle and Berwick Mechanical Services' Work
Truck and Toyota Corolla among them, the latter running back to 2018. The
trial balance holds the defensible position in each case: cost less accumulated
depreciation. The schedule is what drifted.

Two things stop this being a simple subtraction.

Cost less accumulated depreciation is the OPENING written-down value only while
the year's depreciation is still unposted. Once posted, the accumulated account
already contains it and the same subtraction gives the CLOSING value. Reading
one as the other misstates the asset by a full year's charge.

And a depreciation expense account is frequently shared. Dr Services' account
1617 carried 11,147.00 in FY2024 while the vehicle's accumulated depreciation
moved only 4,229.00, so the rest belongs to other assets. Where accounts are
shared, no automated rule can say which asset owns which portion — that is an
accounting judgement, so the audit reports the ambiguity instead of guessing.

Nothing here writes.
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
    TrialBalanceLine,
)


class ReconcileAssetAgainstTrialBalanceTests(TestCase):
    def setUp(self):
        self.client_obj = ClientModel.objects.create(name="Audit Client")
        self.entity = Entity.objects.create(
            entity_name="Audited Pty Ltd", entity_type="company",
            client=self.client_obj)
        self.fy = FinancialYear.objects.create(
            entity=self.entity, year_label="2026",
            start_date=date(2025, 7, 1), end_date=date(2026, 6, 30),
            status=FinancialYear.Status.DRAFT)

    def _tb(self, code, name, debit="0", credit="0", closing=None):
        debit, credit = Decimal(debit), Decimal(credit)
        return TrialBalanceLine.objects.create(
            financial_year=self.fy, account_code=code, account_name=name,
            debit=debit, credit=credit,
            closing_balance=Decimal(closing) if closing is not None else debit - credit)

    def _asset(self, **kw):
        defaults = dict(
            financial_year=self.fy, category="Motor Vehicles",
            asset_name="Vehicle", total_cost=Decimal("0.00"),
            opening_wdv=Decimal("-34136.00"), depreciation_amount=Decimal("-8534.00"),
            closing_wdv=Decimal("-25602.00"), method="D", rate=Decimal("25.00"))
        defaults.update(kw)
        return DepreciationAsset.objects.create(**defaults)

    def _dr_services_shape(self):
        """The real FY2026 position: depreciation not yet posted."""
        self._tb("2890", "Motor vehicles (cost)", debit="38354.00", closing="38354.00")
        self._tb("2895", "Less: Accumulated depreciation", credit="12763.00", closing="-12763.00")
        self._tb("1617", "Depreciation - Other", closing="0.00")
        return self._asset()

    def test_the_trial_balance_position_is_cost_less_accumulated(self):
        from core.depreciation_audit import reconcile_asset
        asset = self._dr_services_shape()

        result = reconcile_asset(asset)

        self.assertEqual(result.tb_cost, Decimal("38354.00"))
        self.assertEqual(result.tb_accumulated, Decimal("12763.00"))
        self.assertEqual(result.tb_written_down_value, Decimal("25591.00"))

    def test_an_unposted_year_reconciles_to_the_opening_value(self):
        """Dr Services FY2026: 1617 is nil, so 25,591 is the opening."""
        from core.depreciation_audit import reconcile_asset
        asset = self._dr_services_shape()

        result = reconcile_asset(asset)

        self.assertEqual(result.proposed_opening_wdv, Decimal("25591.00"))
        self.assertTrue(result.is_reconcilable)

    def test_a_posted_year_reconciles_to_the_closing_value(self):
        """Once depreciation is posted the subtraction gives the closing."""
        from core.depreciation_audit import reconcile_asset
        self._tb("2890", "Motor vehicles (cost)", debit="38354.00", closing="38354.00")
        self._tb("2895", "Less: Accumulated depreciation", credit="12763.00", closing="-12763.00")
        self._tb("1617", "Depreciation - Other", debit="8534.00", closing="8534.00")
        asset = self._asset(depreciation_amount=Decimal("8534.00"))

        result = reconcile_asset(asset)

        self.assertEqual(result.tb_written_down_value, Decimal("25591.00"))
        self.assertEqual(result.proposed_closing_wdv, Decimal("25591.00"))
        self.assertEqual(result.proposed_opening_wdv, Decimal("34125.00"))

    def test_the_current_schedule_values_are_reported_for_comparison(self):
        from core.depreciation_audit import reconcile_asset
        asset = self._dr_services_shape()

        result = reconcile_asset(asset)

        self.assertEqual(result.current_opening_wdv, Decimal("-34136.00"))
        self.assertEqual(result.current_closing_wdv, Decimal("-25602.00"))

    def test_a_schedule_that_already_agrees_needs_no_correction(self):
        from core.depreciation_audit import reconcile_asset
        self._tb("2890", "Motor vehicles (cost)", debit="38354.00", closing="38354.00")
        self._tb("2895", "Less: Accumulated depreciation", credit="12763.00", closing="-12763.00")
        self._tb("1617", "Depreciation - Other", closing="0.00")
        asset = self._asset(opening_wdv=Decimal("25591.00"),
                            depreciation_amount=Decimal("6397.75"),
                            closing_wdv=Decimal("19193.25"))

        self.assertFalse(reconcile_asset(asset).needs_correction)

    def test_a_negative_schedule_needs_correction(self):
        from core.depreciation_audit import reconcile_asset

        self.assertTrue(reconcile_asset(self._dr_services_shape()).needs_correction)

    # -- the cases where an automated answer would be dishonest ------------

    def test_a_missing_cost_account_cannot_be_reconciled(self):
        from core.depreciation_audit import reconcile_asset
        self._tb("1617", "Depreciation - Other", closing="0.00")
        asset = self._asset()

        result = reconcile_asset(asset)

        self.assertFalse(result.is_reconcilable)
        self.assertIn("cost", result.reason.lower())

    def test_two_assets_sharing_one_account_pair_is_flagged_not_guessed(self):
        """Dr Services' 1617 held 11,147 while the vehicle moved 4,229."""
        from core.depreciation_audit import reconcile_asset
        self._tb("2890", "Motor vehicles (cost)", debit="38354.00", closing="38354.00")
        self._tb("2895", "Less: Accumulated depreciation", credit="12763.00", closing="-12763.00")
        self._tb("1617", "Depreciation - Other", closing="0.00")
        first = self._asset(asset_name="Vehicle")
        self._asset(asset_name="Trailer")

        result = reconcile_asset(first)

        self.assertFalse(result.is_reconcilable)
        self.assertIn("shares", result.reason.lower())

    def test_explicit_account_codes_on_the_asset_are_preferred(self):
        from core.depreciation_audit import reconcile_asset
        self._tb("2890", "Motor vehicles (cost)", debit="38354.00", closing="38354.00")
        self._tb("2895", "Less: Accumulated depreciation", credit="12763.00", closing="-12763.00")
        self._tb("2900", "Plant (cost)", debit="99999.00", closing="99999.00")
        self._tb("1617", "Depreciation - Other", closing="0.00")
        asset = self._asset(asset_account_code="2890", accum_dep_code="2895")

        result = reconcile_asset(asset)

        self.assertEqual(result.tb_cost, Decimal("38354.00"))
        self.assertEqual(result.cost_account_code, "2890")

    def test_the_audit_writes_nothing(self):
        from core.depreciation_audit import reconcile_asset
        asset = self._dr_services_shape()
        before = (asset.opening_wdv, asset.depreciation_amount, asset.closing_wdv)

        reconcile_asset(asset)
        asset.refresh_from_db()

        self.assertEqual(
            (asset.opening_wdv, asset.depreciation_amount, asset.closing_wdv), before)

    def test_a_proposed_closing_is_never_derived_from_a_corrupt_charge(self):
        """The schedule's own depreciation is -8,534; using it to derive a
        closing produced 34,125 — higher than the 25,591 opening."""
        from core.depreciation_audit import reconcile_asset
        asset = self._dr_services_shape()

        result = reconcile_asset(asset)

        self.assertLess(result.proposed_closing_wdv, result.proposed_opening_wdv)

    def test_the_proposed_closing_follows_the_assets_own_method_and_rate(self):
        """25% diminishing value on 25,591 is 6,397.75, leaving 19,193.25."""
        from core.depreciation_audit import reconcile_asset
        asset = self._dr_services_shape()

        result = reconcile_asset(asset)

        self.assertEqual(result.proposed_depreciation, Decimal("6397.75"))
        self.assertEqual(result.proposed_closing_wdv, Decimal("19193.25"))


class TheFallbackDoesNotGuessAtAccountsTests(TestCase):
    """Kinross Builders, 2026-09-04.

    Three assets carried no account codes, so the audit matched accounts by
    name. Its cost regex looks for "cost|vehicle|motor|plant|equipment|
    furniture" and takes the lowest matching account code, and account 1740
    "Hire/Rent of plant & equipment" -- a rental expense -- matched on both
    "plant" and "equipment" and sorted ahead of the real 2860 and 2890. The
    accumulated side was no better: 2869 and 2895 both matched "accum" and the
    lower code won regardless of which asset it belonged to. The audit reported
    cost 18,691 against accumulated depreciation 34,081 -- a written-down value
    of -15,390 assembled from two accounts that have nothing to do with each
    other, let alone with the asset.

    Two rules, both of them "do not guess", which is what this module says of
    itself: a P&L account is never an asset's cost account, and a name that
    matches more than one account identifies none of them.
    """

    def setUp(self):
        self.client_obj = ClientModel.objects.create(name="Fallback Client")
        self.entity = Entity.objects.create(
            entity_name="Kinross Builders Pty Ltd", entity_type="company",
            client=self.client_obj)
        self.fy = FinancialYear.objects.create(
            entity=self.entity, year_label="2025",
            start_date=date(2024, 7, 1), end_date=date(2025, 6, 30),
            status=FinancialYear.Status.DRAFT)
        EntityChartOfAccount.objects.filter(entity=self.entity).delete()

    def _tb(self, code, name, closing):
        TrialBalanceLine.objects.create(
            financial_year=self.fy, account_code=code, account_name=name,
            debit=Decimal("0"), credit=Decimal("0"),
            closing_balance=Decimal(closing))

    def _chart(self, code, name, section):
        EntityChartOfAccount.objects.update_or_create(
            entity=self.entity, account_code=code,
            defaults={"account_name": name, "section": section})

    def _asset(self):
        return DepreciationAsset.objects.create(
            financial_year=self.fy, category="Motor Vehicles",
            asset_name="Range Rover AZJ923", total_cost=Decimal("68108.00"),
            opening_wdv=Decimal("68108.00"), method="D", rate=Decimal("25.00"))

    def test_a_rental_expense_is_not_a_candidate_for_an_asset_cost_account(self):
        """1740 is excluded, leaving 2890 as the only candidate."""
        from core.depreciation_audit import reconcile_asset
        self._tb("1740", "Hire/Rent of plant & equipment", "18691.00")
        self._tb("2890", "Motor vehicles (cost)", "76734.00")
        self._tb("2895", "Less: Accumulated depreciation", "-4373.00")
        self._chart("1740", "Hire/Rent of plant & equipment", "expenses")
        self._chart("2890", "Motor vehicles (cost)", "assets")

        result = reconcile_asset(self._asset())

        self.assertEqual(result.cost_account_code, "2890")
        self.assertEqual(result.tb_cost, Decimal("76734.00"))

    def test_two_candidate_cost_accounts_identify_neither(self):
        """Kinross holds both 2860 and 2890; no rule says which is this asset's."""
        from core.depreciation_audit import reconcile_asset
        self._tb("2860", "Plant & equipment (cost)", "34081.00")
        self._tb("2890", "Motor vehicles (cost)", "76734.00")
        self._tb("2895", "Less: Accumulated depreciation", "-4373.00")

        result = reconcile_asset(self._asset())

        self.assertIsNone(result.tb_cost)
        self.assertFalse(result.is_reconcilable)

    def test_two_candidate_accumulated_accounts_identify_neither(self):
        """2869 and 2895 both match "accum"; the lower code is not an answer."""
        from core.depreciation_audit import reconcile_asset
        self._tb("2890", "Motor vehicles (cost)", "76734.00")
        self._tb("2869", "Less: Accumulated depreciation", "-34081.00")
        self._tb("2895", "Less: Accumulated depreciation", "-4373.00")

        result = reconcile_asset(self._asset())

        self.assertIsNone(result.tb_accumulated)
        self.assertFalse(result.is_reconcilable)

    def test_it_never_reports_the_negative_position_it_used_to_assemble(self):
        """The whole Kinross shape: the audit declines instead of inventing."""
        from core.depreciation_audit import reconcile_asset
        self._tb("1740", "Hire/Rent of plant & equipment", "18691.00")
        self._tb("2860", "Plant & equipment (cost)", "34081.00")
        self._tb("2890", "Motor vehicles (cost)", "76734.00")
        self._tb("2869", "Less: Accumulated depreciation", "-34081.00")
        self._tb("2895", "Less: Accumulated depreciation", "-4373.00")
        self._chart("1740", "Hire/Rent of plant & equipment", "expenses")

        result = reconcile_asset(self._asset())

        self.assertIsNone(result.tb_written_down_value)
        self.assertFalse(result.is_reconcilable)

    def test_a_single_unambiguous_match_is_still_used(self):
        """The fallback keeps working where it can only mean one thing."""
        from core.depreciation_audit import reconcile_asset
        self._tb("2890", "Motor vehicles (cost)", "76734.00")
        self._tb("2895", "Less: Accumulated depreciation", "-4373.00")

        result = reconcile_asset(self._asset())

        self.assertEqual(result.cost_account_code, "2890")
        self.assertEqual(result.tb_accumulated, Decimal("4373.00"))
