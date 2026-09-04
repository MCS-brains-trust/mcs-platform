"""Comparatives can be turned off for one financial year without touching the client.

Entity.include_comparative_figures is a client-wide switch, so using it to drop
comparatives from a single year's package also changes what every other year of
that client would produce -- including finalised ones, if anyone regenerates
them. A year that legitimately has no meaningful prior figures is a property of
the year, not of the client.

FinancialYear.include_comparative_figures is therefore a nullable override:
None inherits the client default, True/False decide for that year alone.
Everything reads it through FinancialYear.comparatives_enabled.

_has_prior_year exists as a byte-identical copy in fs_template_service and in
docgen, so both are exercised here. Fixing one and missing the other would let
the on-screen statements and the generated .docx disagree about whether a
comparative column exists, which is the kind of split that surfaces in a
client's hands rather than in a test run.
"""
from datetime import date
from decimal import Decimal

from django.test import TestCase

from core.models import Entity, FinancialYear, TrialBalanceLine


class ComparativesResolutionTests(TestCase):
    def setUp(self):
        self.entity = Entity.objects.create(
            entity_name="Comparatives Pty Ltd", entity_type=Entity.EntityType.COMPANY)
        self.prior = FinancialYear.objects.create(
            entity=self.entity, year_label="2025",
            start_date=date(2024, 7, 1), end_date=date(2025, 6, 30))
        self.fy = FinancialYear.objects.create(
            entity=self.entity, year_label="2026",
            start_date=date(2025, 7, 1), end_date=date(2026, 6, 30),
            prior_year=self.prior)
        TrialBalanceLine.objects.create(
            financial_year=self.prior, account_code="630", account_name="Sales",
            debit=Decimal("0"), credit=Decimal("100.00"), closing_balance=Decimal("-100.00"))

    def _set_entity(self, value):
        self.entity.include_comparative_figures = value
        self.entity.save(update_fields=["include_comparative_figures"])

    def test_a_year_with_no_override_inherits_a_client_default_of_on(self):
        self._set_entity(True)
        self.assertIsNone(self.fy.include_comparative_figures)
        self.assertTrue(self.fy.comparatives_enabled)

    def test_a_year_with_no_override_inherits_a_client_default_of_off(self):
        self._set_entity(False)
        self.assertFalse(self.fy.comparatives_enabled)

    def test_a_year_can_turn_comparatives_off_against_a_client_default_of_on(self):
        self._set_entity(True)
        self.fy.include_comparative_figures = False
        self.assertFalse(self.fy.comparatives_enabled)

    def test_a_year_can_turn_comparatives_on_against_a_client_default_of_off(self):
        self._set_entity(False)
        self.fy.include_comparative_figures = True
        self.assertTrue(self.fy.comparatives_enabled)

    def test_overriding_one_year_leaves_the_client_default_alone(self):
        """The whole point: FY2026 off must not change what FY2025 produces."""
        self._set_entity(True)
        self.fy.include_comparative_figures = False
        self.fy.save(update_fields=["include_comparative_figures"])

        self.entity.refresh_from_db()
        self.prior.refresh_from_db()
        self.assertTrue(self.entity.include_comparative_figures)
        self.assertTrue(self.prior.comparatives_enabled)


class BothCopiesOfHasPriorYearHonourTheOverrideTests(TestCase):
    """fs_template_service and docgen carry identical copies of _has_prior_year."""

    def setUp(self):
        self.entity = Entity.objects.create(
            entity_name="Comparatives Pty Ltd", entity_type=Entity.EntityType.COMPANY)
        self.prior = FinancialYear.objects.create(
            entity=self.entity, year_label="2025",
            start_date=date(2024, 7, 1), end_date=date(2025, 6, 30))
        self.fy = FinancialYear.objects.create(
            entity=self.entity, year_label="2026",
            start_date=date(2025, 7, 1), end_date=date(2026, 6, 30),
            prior_year=self.prior)
        TrialBalanceLine.objects.create(
            financial_year=self.prior, account_code="630", account_name="Sales",
            debit=Decimal("0"), credit=Decimal("100.00"), closing_balance=Decimal("-100.00"))

    def _both(self):
        from core.fs_template_service import _has_prior_year as fs_has
        from core.docgen import _has_prior_year as docgen_has
        return fs_has(self.fy), docgen_has(self.fy)

    def test_both_show_comparatives_when_the_year_inherits_an_enabled_client(self):
        self.assertEqual(self._both(), (True, True))

    def test_both_drop_comparatives_when_the_year_overrides_to_off(self):
        self.fy.include_comparative_figures = False
        self.fy.save(update_fields=["include_comparative_figures"])
        self.assertEqual(self._both(), (False, False))

    def test_both_show_comparatives_when_the_year_overrides_a_disabled_client_to_on(self):
        self.entity.include_comparative_figures = False
        self.entity.save(update_fields=["include_comparative_figures"])
        self.fy.include_comparative_figures = True
        self.fy.save(update_fields=["include_comparative_figures"])
        self.assertEqual(self._both(), (True, True))
