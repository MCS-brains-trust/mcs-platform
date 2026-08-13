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
