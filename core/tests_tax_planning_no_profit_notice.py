"""Where losses absorb the year's income, there is nothing to plan.

The Tax Planning tab is not needed when carried-forward losses leave nil
distributable income. It must say so plainly, stop offering a plan that the
Trust tab's post gate would refuse, and refuse to finalise one.

The finalise gate matters more than the hidden button: check #1 in
``tax_planning_finalise`` is ``undistributed == 0``, and with nil distributable
income and nil proposed distributions that check *passes*. An empty plan
against nil income could be finalised.
"""
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.models import (
    Entity, EntityChartOfAccount, FinancialYear, TaxPlanningWorksheet,
    TrialBalanceLine,
)


class NoProfitToDistributeTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="tpn", email="tpn@example.com", password="secret123",
            role="senior_accountant",
            totp_secret="dummy-secret-for-test", totp_confirmed=True,
        )
        self.client.force_login(self.user)
        session = self.client.session
        session["2fa_verified"] = True
        session.save()

        self.entity = Entity.objects.create(
            entity_name="Loss Carrying Trust", entity_type="trust",
            assigned_accountant=self.user,
        )
        self.fy = FinancialYear.objects.create(
            entity=self.entity, year_label="FY2027",
            start_date=date(2026, 7, 1), end_date=date(2027, 6, 30),
        )
        EntityChartOfAccount.objects.update_or_create(
            entity=self.entity, account_code="400",
            defaults={"account_name": "Sales",
                      "section": EntityChartOfAccount.StatementSection.REVENUE},
        )

    def _earn(self, amount):
        TrialBalanceLine.objects.create(
            financial_year=self.fy, account_code="400", account_name="Sales",
            debit=Decimal("0.00"), credit=Decimal(amount),
            closing_balance=Decimal(amount),
        )

    def _carry_losses(self, amount):
        TrialBalanceLine.objects.create(
            financial_year=self.fy, account_code="4199",
            account_name="Undistributed income", debit=Decimal(amount),
            credit=Decimal("0.00"), closing_balance=Decimal(amount),
            source="rollover",
        )

    def _load_tab(self):
        return self.client.get(
            reverse("core:tax_planning_tab", kwargs={"pk": self.fy.pk}),
            secure=True,
        )

    # --- persistence -----------------------------------------------------

    def test_the_recoupment_figures_are_persisted_on_the_worksheet(self):
        """Section 1 is stored for audit, so its new lines must be too."""
        self._earn("216101.66")
        self._carry_losses("1628428.89")
        self._load_tab()
        w = TaxPlanningWorksheet.objects.get(financial_year=self.fy)
        self.assertEqual(w.income_before_recoupment, Decimal("216101.66"))
        self.assertEqual(w.losses_recouped, Decimal("216101.66"))
        self.assertEqual(w.losses_carried_forward, Decimal("1412327.23"))
        self.assertEqual(w.distributable_income, Decimal("0.00"))

    # --- the notice ------------------------------------------------------

    def test_the_notice_appears_when_losses_absorb_the_income(self):
        self._earn("216101.66")
        self._carry_losses("1628428.89")
        response = self._load_tab()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No profit to distribute")
        self.assertTrue(response.context["no_distributable_income"])

    def test_the_notice_is_absent_when_income_remains(self):
        """Regression guard for every trust that can still distribute."""
        self._earn("100000.00")
        self._carry_losses("30000.00")
        response = self._load_tab()
        self.assertNotContains(response, "No profit to distribute")
        self.assertFalse(response.context["no_distributable_income"])

    def test_a_trust_with_no_losses_at_all_is_unaffected(self):
        self._earn("100000.00")
        response = self._load_tab()
        self.assertNotContains(response, "No profit to distribute")
        self.assertFalse(response.context["no_distributable_income"])

    def test_a_loss_year_with_no_carried_balance_also_shows_the_notice(self):
        """Nil distributable is nil however it arose."""
        TrialBalanceLine.objects.create(
            financial_year=self.fy, account_code="1510",
            account_name="Accountancy", debit=Decimal("5000.00"),
            credit=Decimal("0.00"), closing_balance=Decimal("5000.00"),
        )
        EntityChartOfAccount.objects.update_or_create(
            entity=self.entity, account_code="1510",
            defaults={"account_name": "Accountancy", "section": "expenses"},
        )
        response = self._load_tab()
        self.assertContains(response, "No profit to distribute")

    # --- the lock --------------------------------------------------------

    def test_modelling_is_locked_when_there_is_nothing_to_distribute(self):
        self._earn("216101.66")
        self._carry_losses("1628428.89")
        response = self._load_tab()
        self.assertTrue(response.context["locked"])

    def test_modelling_stays_open_when_income_remains(self):
        self._earn("100000.00")
        response = self._load_tab()
        self.assertFalse(response.context["locked"])

    # --- the finalise gate ----------------------------------------------

    def test_finalising_is_refused_when_nothing_is_distributable(self):
        self._earn("216101.66")
        self._carry_losses("1628428.89")
        self._load_tab()
        response = self.client.post(
            reverse("core:tax_planning_finalise", kwargs={"pk": self.fy.pk}),
            data={}, content_type="application/json", secure=True,
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["success"])
        self.assertTrue(
            any("no profit to distribute" in e.lower()
                for e in payload["errors"]),
            f"expected a nil-income refusal, got {payload['errors']}",
        )
        w = TaxPlanningWorksheet.objects.get(financial_year=self.fy)
        self.assertFalse(w.is_finalised)

    # --- the lock is an invariant, not a UI convention -------------------

    def _nil_year(self):
        self._earn("216101.66")
        self._carry_losses("1628428.89")
        self._load_tab()

    def test_saving_rows_is_refused_when_nothing_is_distributable(self):
        self._nil_year()
        w = TaxPlanningWorksheet.objects.get(financial_year=self.fy)
        response = self.client.post(
            reverse("core:tax_planning_save", kwargs={"pk": self.fy.pk}),
            data={"beneficiary_rows": []}, content_type="application/json",
            secure=True,
        )
        self.assertEqual(
            response.status_code, 400,
            "the tab is locked but the endpoint still accepts writes",
        )

    def test_saving_notes_is_refused_when_nothing_is_distributable(self):
        self._nil_year()
        response = self.client.post(
            reverse("core:tax_planning_save_notes", kwargs={"pk": self.fy.pk}),
            data={"recommendation_notes": "<p>hello</p>"},
            content_type="application/json", secure=True,
        )
        self.assertEqual(response.status_code, 400)
        w = TaxPlanningWorksheet.objects.get(financial_year=self.fy)
        self.assertEqual(w.recommendation_notes, "")

    def test_saving_a_scenario_is_refused_when_nothing_is_distributable(self):
        self._nil_year()
        response = self.client.post(
            reverse("core:tax_planning_scenario_save", kwargs={"pk": self.fy.pk}),
            data={"scenario_name": "Option A", "distributions": []},
            content_type="application/json", secure=True,
        )
        self.assertEqual(response.status_code, 400)

    def test_the_endpoints_stay_open_when_income_remains(self):
        """Regression guard: the lock must not fire on a normal year."""
        self._earn("100000.00")
        self._load_tab()
        response = self.client.post(
            reverse("core:tax_planning_save_notes", kwargs={"pk": self.fy.pk}),
            data={"recommendation_notes": "<p>fine</p>"},
            content_type="application/json", secure=True,
        )
        self.assertEqual(response.status_code, 200)

    def test_finalising_is_refused_even_from_a_stale_worksheet(self):
        """The gate must recompute, not trust the stored figure.

        distributable_income is refreshed only by a GET of the tab, so an
        existing worksheet carries its pre-recoupment value until someone
        opens the page. A finalise POST that skips the GET would evaluate the
        old number -- and report "$0.00 of losses are carried forward",
        because that field is stale too.
        """
        self._earn("216101.66")
        self._carry_losses("1628428.89")
        TaxPlanningWorksheet.objects.create(
            financial_year=self.fy,
            distributable_income=Decimal("216101.66"),  # pre-recoupment
        )
        response = self.client.post(
            reverse("core:tax_planning_finalise", kwargs={"pk": self.fy.pk}),
            data={}, content_type="application/json", secure=True,
        )
        payload = response.json()
        self.assertFalse(payload["success"])
        self.assertTrue(
            any("no profit to distribute" in e.lower()
                for e in payload["errors"]),
            f"the gate trusted a stale snapshot: {payload['errors']}",
        )
        # The figure carried forward AFTER this year recoups its 216,101.66.
        # A stale worksheet's losses_carried_forward is nil, so this also
        # proves the gate recomputed rather than reading the snapshot.
        self.assertTrue(
            any("1,412,327.23" in e for e in payload["errors"]),
            f"the refusal named a stale loss figure: {payload['errors']}",
        )

    def test_finalising_still_works_when_income_is_fully_allocated(self):
        """The new gate must not block a legitimate plan."""
        self._earn("100000.00")
        self._load_tab()
        w = TaxPlanningWorksheet.objects.get(financial_year=self.fy)
        self.assertEqual(w.distributable_income, Decimal("100000.00"))
        # No beneficiaries, so nothing is allocated -- the pre-existing
        # balance check must be what refuses this, not the new gate.
        response = self.client.post(
            reverse("core:tax_planning_finalise", kwargs={"pk": self.fy.pk}),
            data={}, content_type="application/json", secure=True,
        )
        payload = response.json()
        self.assertFalse(
            any("no profit to distribute" in e.lower()
                for e in payload["errors"]),
            "the nil-income gate fired on a year that has income",
        )
