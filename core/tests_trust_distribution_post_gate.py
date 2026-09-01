"""Posting a distribution must recheck distributable income against the ledger.

``_calculate_income_streams`` already recoups a brought-forward loss before
offering anything for distribution -- see
tests_trust_distributable_income_recoupment. But that figure is computed once,
stored on TrustWorkspace.net_distributable_income, and never looked at again.
``trust_post_distribution`` gates on a scenario being selected, on no live
distribution already existing, and on every beneficiary having a 4004.NN loan
account. It never checks how much is actually distributable.

Minli Enterprise Unit Trust FY2026 is what that costs. Its workspace was
computed at 2026-08-27 02:17 UTC and stored 876,322.95. The recoupment rule
landed at 11:18 that day and the income-streams netting fix later still, so
that figure came from two superseded calculations and reconciles to neither.
JE-007 posted 626,802.51 from it at 2026-08-28 01:42 -- into a year whose
distributable income, recomputed, is nil, against 2,255,231.40 of losses
carried forward in 4199. The profit went to the two unitholders instead of
reducing the deficit, and the year was finalised 33 seconds later.

A stored figure is only ever a snapshot. The gate recomputes.
"""
import json
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse

from core.models import (
    AccountMapping, AdjustingJournal, EntityOfficer, FinancialYear,
    TaxPlanningScenario, TrialBalanceLine, TrustWorkspace,
)
from core.tests_beneficiary_accounts import BeneficiaryAccountTestBase

STORAGES_OVERRIDE = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

D = Decimal


@override_settings(STORAGES=STORAGES_OVERRIDE)
class DistributionPostGateTestsBase(BeneficiaryAccountTestBase):
    """One beneficiary, one scenario, an authenticated 2FA-verified user.

    Auth setup mirrors tests_trust_distribution_naming: 2fa_verified after
    force_login, secure=True on the POST.
    """

    # Subclasses set these before calling _setup().
    profit = D("0")
    brought_forward = D("0")
    allocation = "0.00"
    stored_ndi = None

    def _mapping(self, section):
        m, _ = AccountMapping.objects.get_or_create(
            standard_code=f"GATE-{section}",
            defaults={"line_item_label": section.title(),
                      "financial_statement": "income_statement",
                      "statement_section": section},
        )
        return m

    def setUp(self):
        self.officer = EntityOfficer.objects.create(
            entity=self.trust, full_name="Gate Bene",
            role=EntityOfficer.OfficerRole.BENEFICIARY,
            beneficiary_type="adult",
        )
        self.fy = FinancialYear.objects.create(
            entity=self.trust, year_label="FY2026",
            start_date=date(2025, 7, 1), end_date=date(2026, 6, 30),
        )

        # Revenue carrying the whole profit, so net_profit == self.profit.
        if self.profit:
            TrialBalanceLine.objects.create(
                financial_year=self.fy, account_code="0630",
                account_name="Sales", closing_balance=-self.profit,
                credit=self.profit, source="tb_import",
                mapped_line_item=self._mapping("revenue"),
            )
        # 4199 rollover is the brought-forward position, debit-positive.
        if self.brought_forward:
            TrialBalanceLine.objects.create(
                financial_year=self.fy, account_code="4199",
                account_name="Undistributed income",
                closing_balance=self.brought_forward,
                debit=self.brought_forward, source="rollover",
            )

        self.workspace = TrustWorkspace.objects.create(financial_year=self.fy)
        self.scenario = TaxPlanningScenario.objects.create(
            financial_year=self.fy, scenario_name="Gate scenario",
            distributions=[{
                "beneficiary_id": str(self.officer.pk),
                "proposed_distribution": self.allocation,
            }],
        )
        self.workspace.selected_tax_scenario = self.scenario
        if self.stored_ndi is not None:
            self.workspace.net_distributable_income = self.stored_ndi
        self.workspace.save()

        User = get_user_model()
        self.user = User.objects.create_user(
            username="gateposter", password="pw", email="gp@example.com",
            role=User.Role.ADMIN,
            totp_secret="dummy-secret-gateposter", totp_confirmed=True,
        )
        self.client.force_login(self.user)
        session = self.client.session
        session["2fa_verified"] = True
        session.save()

    def _post(self):
        return self.client.post(
            reverse("core:trust_post_distribution", args=[self.fy.pk]),
            data=json.dumps({}), content_type="application/json", secure=True,
        )

    def assertNothingPosted(self):
        self.assertIsNone(
            AdjustingJournal.live_trust_distribution(self.fy),
            "a distribution journal was posted despite the gate",
        )


class LossCarriedForwardBlocksDistributionTests(DistributionPostGateTestsBase):
    """The Minli shape: profit fully absorbed by a brought-forward loss."""

    profit = D("626802.51")
    brought_forward = D("2255231.40")
    allocation = "626802.51"

    def test_posting_is_refused(self):
        response = self._post()
        self.assertEqual(response.status_code, 400)
        self.assertNothingPosted()

    def test_the_error_names_the_figures(self):
        body = self._post().json()
        self.assertIn("error", body)
        message = body["error"]
        self.assertIn("2,255,231.40", message)
        self.assertIn("626,802.51", message)


class StoredWorkspaceFigureIsNotTrustedTests(DistributionPostGateTestsBase):
    """Minli's workspace held 876,322.95 from superseded calculations."""

    profit = D("626802.51")
    brought_forward = D("2255231.40")
    allocation = "626802.51"
    stored_ndi = D("876322.95")

    def test_the_gate_recomputes_from_the_ledger(self):
        self.assertEqual(
            self.workspace.net_distributable_income, D("876322.95"),
            "fixture guard: the stale figure must be on the workspace",
        )
        self.assertEqual(self._post().status_code, 400)
        self.assertNothingPosted()


class AllocationAboveDistributableIsRefusedTests(DistributionPostGateTestsBase):
    """Partial recoupment: 1,000 profit less 400 carried forward leaves 600."""

    profit = D("1000.00")
    brought_forward = D("400.00")
    allocation = "1000.00"

    def test_posting_more_than_distributable_is_refused(self):
        self.assertEqual(self._post().status_code, 400)
        self.assertNothingPosted()


class DistributionWithinDistributableStillPostsTests(DistributionPostGateTestsBase):
    """The gate must not block a legitimate distribution."""

    profit = D("1000.00")
    brought_forward = D("400.00")
    allocation = "600.00"

    def test_posting_up_to_distributable_succeeds(self):
        response = self._post()
        self.assertEqual(
            response.status_code, 200,
            f"legitimate distribution refused: {response.content[:300]}",
        )
        journal = AdjustingJournal.live_trust_distribution(self.fy)
        self.assertIsNotNone(journal, "no distribution journal was created")
        self.assertEqual(journal.total_debit, D("600.00"))


class NoBroughtForwardLossPostsNormallyTests(DistributionPostGateTestsBase):
    """A trust with no deficit distributes its whole profit, as before."""

    profit = D("5000.00")
    allocation = "5000.00"

    def test_full_profit_may_be_distributed(self):
        response = self._post()
        self.assertEqual(
            response.status_code, 200,
            f"unexpectedly refused: {response.content[:300]}",
        )
        self.assertIsNotNone(AdjustingJournal.live_trust_distribution(self.fy))
