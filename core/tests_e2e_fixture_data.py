"""Tests for the deterministic Tier 2 fixture entity."""
from decimal import Decimal

from django.test import TestCase

from core.e2e_fixture_data import FIXTURE_IDS, seed_fixture_entity
from core.models import (
    DepreciationAsset,
    Entity,
    EntityChartOfAccount,
    FinancialYear,
    TrialBalanceLine,
)


class SeedFixtureEntityTests(TestCase):
    def test_creates_entity_with_fixed_ids(self):
        seed_fixture_entity()
        entity = Entity.objects.get(pk=FIXTURE_IDS["entity"])
        self.assertEqual(entity.entity_type, "company")
        self.assertEqual(entity.entity_name, "E2E Fixture Holdings Pty Ltd")

    def test_prior_year_is_finalised_and_current_year_is_draft(self):
        seed_fixture_entity()
        prior = FinancialYear.objects.get(pk=FIXTURE_IDS["prior_fy"])
        current = FinancialYear.objects.get(pk=FIXTURE_IDS["current_fy"])
        self.assertEqual(prior.status, FinancialYear.Status.FINALISED)
        self.assertEqual(current.status, FinancialYear.Status.DRAFT)
        self.assertEqual(current.prior_year_id, prior.pk)

    def test_prior_year_trial_balance_balances(self):
        seed_fixture_entity()
        lines = TrialBalanceLine.objects.filter(financial_year_id=FIXTURE_IDS["prior_fy"])
        self.assertEqual(
            sum(line.debit for line in lines),
            sum(line.credit for line in lines),
        )

    def test_seeds_depreciation_accounts_so_posting_can_resolve_them(self):
        # _resolve_depreciation_account_groups falls back to EntityChartOfAccount
        # matched on name, so both accounts must exist or post-to-TB errors out.
        seed_fixture_entity()
        names = set(
            EntityChartOfAccount.objects.filter(
                entity_id=FIXTURE_IDS["entity"]
            ).values_list("account_name", flat=True)
        )
        self.assertIn("Depreciation", names)
        self.assertIn("Accumulated Depreciation", names)

    def test_asset_has_non_zero_business_depreciation(self):
        seed_fixture_entity()
        asset = DepreciationAsset.objects.get(pk=FIXTURE_IDS["asset"])
        business = asset.depreciation_amount - asset.private_depreciation
        self.assertEqual(business, Decimal("4000.00"))

    def test_is_idempotent(self):
        seed_fixture_entity()
        seed_fixture_entity()
        self.assertEqual(Entity.objects.filter(pk=FIXTURE_IDS["entity"]).count(), 1)
        self.assertEqual(
            TrialBalanceLine.objects.filter(financial_year_id=FIXTURE_IDS["prior_fy"]).count(),
            5,
        )
