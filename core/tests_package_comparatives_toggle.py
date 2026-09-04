"""Comparatives can be turned on or off from the package assembly page.

The switch belongs here because that is where the accountant decides what the
client actually receives. It writes the year's own override, never the client
default, so turning it off for one package cannot quietly change what any other
year of that client would produce.

Financial statements are generated before this stage, so a .docx already on disk
does not change when the box is ticked. GeneratedDocument.comparatives_included
records what was in force when each document was built, and the page says the
statements need regenerating when that no longer matches. Documents generated
before the field existed hold NULL and are treated as unknown rather than stale,
so the change does not make every client's existing package announce itself as
out of date.
"""
from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.models import (
    Entity, FinancialYear, GeneratedDocument, TrialBalanceLine,
)
from core.test_support import Require2FAMixin


class PackageComparativesToggleTests(Require2FAMixin, TestCase):
    def setUp(self):
        self.entity = Entity.objects.create(
            entity_name="Package Pty Ltd", entity_type=Entity.EntityType.COMPANY,
            include_comparative_figures=True)
        self.prior = FinancialYear.objects.create(
            entity=self.entity, year_label="2025",
            start_date=date(2024, 7, 1), end_date=date(2025, 6, 30))
        self.fy = FinancialYear.objects.create(
            entity=self.entity, year_label="2026",
            start_date=date(2025, 7, 1), end_date=date(2026, 6, 30),
            prior_year=self.prior, status=FinancialYear.Status.FINALISED)
        TrialBalanceLine.objects.create(
            financial_year=self.prior, account_code="630", account_name="Sales",
            debit=0, credit=100, closing_balance=-100)
        User = get_user_model()
        self.user = User.objects.create_user(
            username="acct", email="acct@example.com", password="pw",
            role="accountant", totp_secret="TESTSECRET", totp_confirmed=True)
        self.entity.assigned_accountant = self.user
        self.entity.save(update_fields=["assigned_accountant"])
        self.login_as(self.user)

    def _page(self):
        return self.client.get(
            reverse("core:package_assembly", args=[self.fy.pk]), secure=True)

    def _toggle(self, enabled):
        return self.client.post(
            reverse("core:package_comparatives", args=[self.fy.pk]),
            {"enabled": "1" if enabled else "0"}, secure=True)

    def _fs(self, comparatives_included):
        return GeneratedDocument.objects.create(
            financial_year=self.fy,
            document_type=GeneratedDocument.DocumentType.FINANCIAL_STATEMENTS,
            comparatives_included=comparatives_included)

    # ---- the switch itself -------------------------------------------------

    def test_the_page_reports_comparatives_on_when_the_client_default_is_on(self):
        response = self._page()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["comparatives_enabled"])

    def test_turning_comparatives_off_is_recorded_against_the_year(self):
        self._toggle(False)

        self.fy.refresh_from_db()
        self.assertIs(self.fy.include_comparative_figures, False)
        self.assertFalse(self.fy.comparatives_enabled)

    def test_turning_comparatives_off_leaves_the_client_default_alone(self):
        self._toggle(False)

        self.entity.refresh_from_db()
        self.assertTrue(self.entity.include_comparative_figures)
        self.assertTrue(self.prior.comparatives_enabled)

    def test_turning_them_back_on_overrides_a_client_default_of_off(self):
        self.entity.include_comparative_figures = False
        self.entity.save(update_fields=["include_comparative_figures"])

        self._toggle(True)

        self.fy.refresh_from_db()
        self.assertIs(self.fy.include_comparative_figures, True)
        self.assertTrue(self.fy.comparatives_enabled)

    # ---- staleness ---------------------------------------------------------

    def test_statements_built_under_the_current_setting_are_not_stale(self):
        self._fs(comparatives_included=True)

        self.assertFalse(self._page().context["comparatives_stale"])

    def test_statements_built_under_the_opposite_setting_are_stale(self):
        self._fs(comparatives_included=True)
        self._toggle(False)

        self.assertTrue(self._page().context["comparatives_stale"])

    def test_a_document_that_predates_the_field_is_not_called_stale(self):
        """NULL is unknown. Every existing document holds it."""
        self._fs(comparatives_included=None)
        self._toggle(False)

        self.assertFalse(self._page().context["comparatives_stale"])

    def test_nothing_is_stale_when_no_statements_have_been_generated(self):
        self._toggle(False)

        self.assertFalse(self._page().context["comparatives_stale"])
