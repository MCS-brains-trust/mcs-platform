"""The distribution journal must credit the beneficiary loan liability.

Auth setup mirrors core/tests_bas_reallocate_posting.py: can_view_all_entities
is a read-only property derived from role, Require2FAMiddleware needs
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
class DistributionPostingCreditsLiabilityTests(BeneficiaryAccountTestBase):
    """Step 5 from the brief: the posting endpoint must credit 4110, not
    4004, and must not overwrite the chart's 4199 name. Written first (per
    the controller's ruling) so it fails red against the pre-fix code,
    which still resolves the credit via account_code__startswith="4004"."""

    def setUp(self):
        self.officer = EntityOfficer.objects.create(
            entity=self.trust, full_name="Liability Bene",
            role=EntityOfficer.OfficerRole.BENEFICIARY,
            beneficiary_type="adult",
        )
        # 4199 isn't one of BENEFICIARY_PARENT_CODES, so
        # BeneficiaryAccountTestBase doesn't seed it directly — but the
        # Entity post_save signal (handle_trust_entity_created) already
        # seeded it from the master ChartOfAccount template (migration 0148,
        # "Undistributed income") when self.trust was created. Confirm that
        # so the posting view's chart lookup exercises the real lookup path,
        # not just its fallback default.
        self.assertTrue(EntityChartOfAccount.objects.filter(
            entity=self.trust, account_code="4199",
            account_name="Undistributed income",
        ).exists(), "expected 4199 to be auto-seeded from the trust template")
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
            username="distposter", password="pw", email="dp@example.com",
            role=User.Role.ADMIN,
            totp_secret="dummy-secret-distposter", totp_confirmed=True,
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

    def test_credit_lines_land_on_4110_and_debit_keeps_chart_name(self):
        response = self._post_distribution()
        self.assertEqual(response.status_code, 200, response.content)

        journal = self.fy.adjusting_journals.get(is_trust_distribution=True)

        credit_lines = JournalLine.objects.filter(
            journal=journal, credit__gt=0,
        )
        self.assertTrue(credit_lines.exists())
        self.assertTrue(all(
            l.account_code.startswith("4110") for l in credit_lines
        ), [l.account_code for l in credit_lines])

        debit_line = JournalLine.objects.get(journal=journal, debit__gt=0)
        self.assertEqual(debit_line.account_code, "4199")
        self.assertEqual(debit_line.account_name, "Undistributed income")


@override_settings(STORAGES=STORAGES_OVERRIDE)
class BeneficiaryLoanAccountProvisioningTests(BeneficiaryAccountTestBase):
    """Step 1 from the brief, kept as a supporting precondition test: every
    beneficiary already has a 4110.NN liability account provisioned, which
    is what the fixed resolution logic depends on."""

    def test_beneficiary_has_a_4110_liability_account(self):
        officer = EntityOfficer.objects.create(
            entity=self.trust, full_name="Liability Bene",
            role=EntityOfficer.OfficerRole.BENEFICIARY,
            beneficiary_type="adult",
        )
        loan = EntityChartOfAccount.objects.get(
            entity=self.trust, beneficiary_officer=officer,
            account_code__startswith="4110",
        )
        self.assertEqual(loan.section, "liabilities")
