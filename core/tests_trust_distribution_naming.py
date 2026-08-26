"""The distribution journal's 4199 debit line must take its name from the
entity's own chart of accounts, not a hardcoded string.

HandiLedger — the accounting system this app must copy — names account 4199
"Undistributed income" for trusts, and every trust's chart in the database
says exactly that. The debit line used to hardcode
account_name="Profit Distribution — Appropriation", which then won the
equity row on the client's balance sheet (the statement builder aggregates
trial balance rows by account code).

Auth setup mirrors core/tests_bas_reallocate_posting.py: can_edit is a
read-only property derived from role, Require2FAMiddleware needs
2fa_verified set after force_login, and SECURE_SSL_REDIRECT 301s any POST
without secure=True.
"""
import json
from datetime import date

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse

from core.models import (
    EntityChartOfAccount, EntityOfficer, FinancialYear, JournalLine,
    TaxPlanningScenario, TrustWorkspace,
)
from core.tests_beneficiary_accounts import BeneficiaryAccountTestBase

STORAGES_OVERRIDE = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@override_settings(STORAGES=STORAGES_OVERRIDE)
class DistributionDebitLineNamingTestsBase(BeneficiaryAccountTestBase):
    """Shared posting scaffold: one beneficiary, one scenario, one workspace,
    one authenticated 2FA-verified user."""

    def setUp(self):
        self.officer = EntityOfficer.objects.create(
            entity=self.trust, full_name="Naming Bene",
            role=EntityOfficer.OfficerRole.BENEFICIARY,
            beneficiary_type="adult",
        )
        self.fy = FinancialYear.objects.create(
            entity=self.trust,
            year_label="FY2025",
            start_date=date(2024, 7, 1),
            end_date=date(2025, 6, 30),
        )
        self.workspace = TrustWorkspace.objects.create(financial_year=self.fy)
        self.scenario = TaxPlanningScenario.objects.create(
            financial_year=self.fy,
            scenario_name="Base scenario",
            distributions=[{
                "beneficiary_id": str(self.officer.pk),
                "proposed_distribution": "10000.00",
            }],
        )
        self.workspace.selected_tax_scenario = self.scenario
        self.workspace.save(update_fields=["selected_tax_scenario"])

        User = get_user_model()
        self.user = User.objects.create_user(
            username="namingposter", password="pw", email="np@example.com",
            role=User.Role.ADMIN,
            totp_secret="dummy-secret-namingposter", totp_confirmed=True,
        )
        self.client.force_login(self.user)
        session = self.client.session
        session["2fa_verified"] = True
        session.save()

    def _post_distribution(self):
        return self.client.post(
            reverse("core:trust_post_distribution", args=[self.fy.pk]),
            data=json.dumps({}),
            content_type="application/json",
            secure=True,
        )


class DistributionDebitLineTakesChartNameTests(DistributionDebitLineNamingTestsBase):
    """The 4199 debit line's account_name must come from the entity's chart,
    not a hardcoded string — proven with a chart name distinct from both the
    old hardcoded value and the fallback default, so a coincidental match
    can't hide a regression."""

    def setUp(self):
        super().setUp()
        # Trust entities auto-seed 4199 from the master template as
        # "Undistributed income" (core/signals.py handle_trust_entity_created).
        # Overwrite it with a value distinct from both the old hardcoded
        # string and the fallback default, so the assertion below can only
        # pass if the view actually reads the chart.
        EntityChartOfAccount.objects.filter(
            entity=self.trust, account_code="4199",
        ).update(account_name="Custom Trust Appropriation Label")

    def test_debit_line_name_comes_from_entity_chart(self):
        response = self._post_distribution()
        self.assertEqual(response.status_code, 200, response.content)

        journal = self.fy.adjusting_journals.get(is_trust_distribution=True)
        debit_line = JournalLine.objects.get(journal=journal, debit__gt=0)

        self.assertEqual(debit_line.account_code, "4199")
        self.assertEqual(
            debit_line.account_name, "Custom Trust Appropriation Label",
        )
        self.assertNotEqual(
            debit_line.account_name, "Profit Distribution — Appropriation",
        )

    def test_credit_line_lands_on_4004_loan_account(self):
        """Sanity check that this test drives the real posting path (credit
        side untouched by this fix) — not asserting on it beyond the code
        prefix, per the brief."""
        response = self._post_distribution()
        self.assertEqual(response.status_code, 200, response.content)

        journal = self.fy.adjusting_journals.get(is_trust_distribution=True)
        credit_lines = JournalLine.objects.filter(journal=journal, credit__gt=0)
        self.assertTrue(credit_lines.exists())
        self.assertTrue(all(
            l.account_code.startswith("4004") for l in credit_lines
        ), [l.account_code for l in credit_lines])


class DistributionDebitLineFallbackNameTests(DistributionDebitLineNamingTestsBase):
    """An entity with no 4199 chart row at all must still post — falling
    back to "Undistributed income" — without raising."""

    def setUp(self):
        super().setUp()
        # Simulate an entity with no 4199 row in its chart at all (not just
        # an unmodified default), exercising the `if _appropriation else`
        # branch rather than its truthy path.
        EntityChartOfAccount.objects.filter(
            entity=self.trust, account_code="4199",
        ).delete()

    def test_posts_with_fallback_name_when_chart_has_no_4199_row(self):
        self.assertFalse(EntityChartOfAccount.objects.filter(
            entity=self.trust, account_code="4199",
        ).exists())

        response = self._post_distribution()
        self.assertEqual(response.status_code, 200, response.content)

        journal = self.fy.adjusting_journals.get(is_trust_distribution=True)
        debit_line = JournalLine.objects.get(journal=journal, debit__gt=0)

        self.assertEqual(debit_line.account_code, "4199")
        self.assertEqual(debit_line.account_name, "Undistributed income")
