"""The fixture profiles seed exactly what each entity type's real chart looks like.

The company tests here are a refactor guard rather than new coverage: the company
profile must keep seeding byte-identical rows, because its Tier 2 checkpoints are
already blessed in e2e/tier2/figures.baseline.json. If any figure in it moves, the
baseline it was blessed against is no longer describing the same fixture.

The per-type classes that follow are the actual new coverage. Each one pins the
structure that distinguishes its entity type -- see
docs/superpowers/specs/2026-08-12-tier2-entity-type-fixtures-design.md for where
each chart came from. All codes and sections are taken from a real exemplar entity
in the production copy, not invented.
"""
from decimal import Decimal

from django.test import TestCase

from core.e2e_fixture_data import PROFILES, seed_fixture_entity


class CompanyProfileUnchangedTests(TestCase):
    def test_the_company_profile_seeds_the_same_seven_trial_balance_lines(self):
        from core.models import TrialBalanceLine

        ids = seed_fixture_entity(PROFILES["company"])
        lines = {
            line.account_code: line
            for line in TrialBalanceLine.objects.filter(financial_year=ids["prior_fy"])
        }
        self.assertEqual(len(lines), 7)
        self.assertEqual(lines["1-1000"].debit, Decimal("70000.00"))
        self.assertEqual(lines["3-1000"].credit, Decimal("60000.00"))
        self.assertEqual(lines["4-1000"].credit, Decimal("30000.00"))
        self.assertEqual(lines["6-1000"].debit, Decimal("10000.00"))
        self.assertEqual(
            sum(line.debit for line in lines.values()),
            sum(line.credit for line in lines.values()),
        )

    def test_the_company_profile_seeds_its_eight_chart_accounts(self):
        from core.models import EntityChartOfAccount

        ids = seed_fixture_entity(PROFILES["company"])
        codes = set(
            EntityChartOfAccount.objects.filter(entity_id=ids["entity"]).values_list(
                "account_code", flat=True
            )
        )
        self.assertEqual(
            codes,
            {
                "1-1000",
                "1-2000",
                "1-2100",
                "2-1000",
                "3-1000",
                "4-1000",
                "6-1000",
                "6-1200",
            },
        )

    def test_the_company_client_name_is_unchanged(self):
        """Deliberately unsuffixed. Only the new profiles carry a key suffix, so the
        company's seeded rows stay identical to the pre-refactor fixture."""
        from core.models import Client

        ids = seed_fixture_entity(PROFILES["company"])
        self.assertEqual(Client.objects.get(pk=ids["client"]).name, "E2E Fixture Client")

    def test_seeding_is_idempotent(self):
        from core.models import TrialBalanceLine

        ids = seed_fixture_entity(PROFILES["company"])
        seed_fixture_entity(PROFILES["company"])
        self.assertEqual(
            TrialBalanceLine.objects.filter(financial_year=ids["prior_fy"]).count(), 7
        )

    def test_the_default_argument_is_still_the_company(self):
        ids_default = seed_fixture_entity()
        ids_explicit = seed_fixture_entity(PROFILES["company"])
        self.assertEqual(ids_default["entity"], ids_explicit["entity"])

    def test_a_profile_can_be_named_by_string(self):
        ids_by_string = seed_fixture_entity("company")
        self.assertEqual(ids_by_string["entity"], PROFILES["company"].ids["entity"])


class TrustProfileTests(TestCase):
    """Modelled on E & J Chiaravalle Family Trust: beneficiary sub-accounts at
    .01/.02, and 4199 Undistributed income sitting in pl_appropriation rather than
    capital_accounts -- which is where the partnership and sole trader put theirs."""

    def test_the_prior_year_trial_balance_balances(self):
        from core.models import TrialBalanceLine

        ids = seed_fixture_entity(PROFILES["trust"])
        lines = TrialBalanceLine.objects.filter(financial_year=ids["prior_fy"])
        self.assertEqual(
            sum(line.debit for line in lines), sum(line.credit for line in lines)
        )
        self.assertEqual(sum(line.debit for line in lines), Decimal("100000.00"))

    def test_both_beneficiaries_have_their_own_sub_coded_capital_account(self):
        from core.models import EntityChartOfAccount

        ids = seed_fixture_entity(PROFILES["trust"])
        codes = set(
            EntityChartOfAccount.objects.filter(
                entity_id=ids["entity"], section="capital_accounts"
            ).values_list("account_code", flat=True)
        )
        self.assertIn("4000.01", codes)
        self.assertIn("4000.02", codes)
        self.assertIn("4005.01", codes)
        self.assertIn("4005.02", codes)

    def test_undistributed_income_is_in_the_pl_appropriation_section(self):
        from core.models import EntityChartOfAccount

        ids = seed_fixture_entity(PROFILES["trust"])
        account = EntityChartOfAccount.objects.get(
            entity_id=ids["entity"], account_code="4199"
        )
        self.assertEqual(account.account_name, "Undistributed income")
        self.assertEqual(account.section, "pl_appropriation")
        self.assertEqual(PROFILES["trust"].retained_profits_code, "4199")

    def test_the_entity_is_a_trust_with_a_valid_abn(self):
        from core.models import Entity
        from core.validators import is_valid_abn

        ids = seed_fixture_entity(PROFILES["trust"])
        entity = Entity.objects.get(pk=ids["entity"])
        self.assertEqual(entity.entity_type, "trust")
        self.assertTrue(is_valid_abn(entity.abn))


class PartnershipProfileTests(TestCase):
    """Modelled on D.P Vaughan & D Vriend: partner sub-accounts at .01/.02 as the
    trust has, but 4199 Unappropriated profits in capital_accounts -- so this
    fixture isolates the sub-account question from the section question."""

    def test_the_prior_year_trial_balance_balances(self):
        from core.models import TrialBalanceLine

        ids = seed_fixture_entity(PROFILES["partnership"])
        lines = TrialBalanceLine.objects.filter(financial_year=ids["prior_fy"])
        self.assertEqual(
            sum(line.debit for line in lines), sum(line.credit for line in lines)
        )
        self.assertEqual(sum(line.debit for line in lines), Decimal("100000.00"))

    def test_both_partners_have_opening_balance_and_share_of_profit_accounts(self):
        from core.models import EntityChartOfAccount

        ids = seed_fixture_entity(PROFILES["partnership"])
        codes = set(
            EntityChartOfAccount.objects.filter(entity_id=ids["entity"]).values_list(
                "account_code", flat=True
            )
        )
        for code in ("4000.01", "4000.02", "4003.01", "4003.02", "4054.01", "4054.02"):
            self.assertIn(code, codes)

    def test_unappropriated_profits_is_the_retained_profits_account(self):
        from core.models import EntityChartOfAccount

        ids = seed_fixture_entity(PROFILES["partnership"])
        account = EntityChartOfAccount.objects.get(
            entity_id=ids["entity"], account_code="4199"
        )
        self.assertEqual(account.account_name, "Unappropriated profits")
        self.assertEqual(account.section, "capital_accounts")
        self.assertEqual(PROFILES["partnership"].retained_profits_code, "4199")


class SoleTraderProfileTests(TestCase):
    """Modelled on Daniel Habteslassie: no sub-coded capital accounts at all -- the
    real chart has none -- and a 2850/2859 plant pairing rather than 2860/2869."""

    def test_the_prior_year_trial_balance_balances(self):
        from core.models import TrialBalanceLine

        ids = seed_fixture_entity(PROFILES["sole_trader"])
        lines = TrialBalanceLine.objects.filter(financial_year=ids["prior_fy"])
        self.assertEqual(
            sum(line.debit for line in lines), sum(line.credit for line in lines)
        )
        self.assertEqual(sum(line.debit for line in lines), Decimal("100000.00"))

    def test_no_capital_account_is_sub_coded(self):
        from core.models import EntityChartOfAccount

        ids = seed_fixture_entity(PROFILES["sole_trader"])
        codes = EntityChartOfAccount.objects.filter(
            entity_id=ids["entity"], section="capital_accounts"
        ).values_list("account_code", flat=True)
        self.assertTrue(codes)
        for code in codes:
            self.assertNotIn(".", code)

    def test_undistributed_income_is_the_retained_profits_account(self):
        from core.models import EntityChartOfAccount

        ids = seed_fixture_entity(PROFILES["sole_trader"])
        account = EntityChartOfAccount.objects.get(
            entity_id=ids["entity"], account_code="4199"
        )
        self.assertEqual(account.account_name, "Undistributed income")
        self.assertEqual(account.section, "capital_accounts")
        self.assertEqual(PROFILES["sole_trader"].retained_profits_code, "4199")


class AllProfilesTests(TestCase):
    """Invariants every profile has to satisfy, so a new one cannot be added
    carelessly."""

    def test_every_profile_has_a_distinct_set_of_fixed_ids(self):
        seen = set()
        for profile in PROFILES.values():
            for value in profile.ids.values():
                self.assertNotIn(value, seen, f"{profile.key} reuses id {value}")
                seen.add(value)

    def test_every_profile_declares_a_retained_profits_account_in_its_own_chart(self):
        for profile in PROFILES.values():
            codes = {code for code, _name, _section in profile.chart}
            self.assertIn(
                profile.retained_profits_code,
                codes,
                f"{profile.key}'s retained_profits_code is not in its chart",
            )

    def test_every_profiles_prior_year_trial_balance_balances(self):
        for profile in PROFILES.values():
            debits = sum(debit for _c, _n, debit, _cr in profile.prior_year_tb)
            credits = sum(credit for _c, _n, _d, credit in profile.prior_year_tb)
            self.assertEqual(debits, credits, f"{profile.key}'s prior TB is unbalanced")

    def test_every_trial_balance_account_exists_in_its_chart(self):
        for profile in PROFILES.values():
            chart_codes = {code for code, _name, _section in profile.chart}
            for code, _name, _debit, _credit in profile.prior_year_tb:
                self.assertIn(
                    code, chart_codes, f"{profile.key}: {code} is not in its chart"
                )
