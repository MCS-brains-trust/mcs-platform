"""Whole-branch review fixes: the findings no per-task review could see.

Each class here pins one finding from the branch-level review of the
unit-trust work. They are gathered in one module because what they have in
common is the reason they were missed: every one of them lives in the seam
between two tasks that were each individually correct.

  * ``UnitTrustCanObtainAScenarioTests`` (C2) -- Task 9 hid Section 4 of the
    tax planning template for a unit trust, and Section 4's
    ``#btnSaveScenario`` is the ONLY control in the codebase that creates a
    ``TaxPlanningScenario``. Every client-facing product of a distribution
    reads ``TrustWorkspace.selected_tax_scenario``, which can only point at
    a saved scenario: the distribution journal (hard 400 without one), the
    minutes table, the holder statements, and the capital accounts note --
    where a missing scenario leaves ``profit_dist`` at 0 and the year's
    distribution is then derived as funds LOANED to the trust on the face
    of the financial statements. Minli can do all of this today as
    entity_type='trust', so hiding save was a regression on a live client.
    The MODELLING stays hidden; the SAVE control comes back.

  * ``UnitTrustDistributionFormRendersTests`` (C2, second half) -- Task 8
    wired ``allocate_unit_trust_distribution`` into
    ``trust_distribution``'s save_allocations branch, but the view built
    its ``beneficiaries`` list from ``role="beneficiary"`` only, and the
    template wraps the whole form INCLUDING its submit button in
    ``{% if beneficiaries %}``. The two existing wiring tests POST the URL
    directly, so they passed against a form that never rendered.

  * ``UnitHolderOnADiscretionaryTrustKeepsItsHistoryTests`` (I1)
  * ``FinalisedWorksheetIsNotRewrittenOnGetTests`` (I2)
  * ``OddCentFollowsTheDisplayedPercentageTests`` (I3)
  * ``CeasedHolderMessageTests`` (I5)
"""
import json
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse

from core.models import (
    Entity,
    EntityChartOfAccount,
    EntityOfficer,
    FinancialYear,
    OfficerDistributionHistory,
    TaxPlanningBeneficiaryRow,
    TaxPlanningScenario,
    TaxPlanningWorksheet,
    TrialBalanceLine,
    TrustDistribution,
)


def _logged_in_admin(client, username):
    User = get_user_model()
    user = User.objects.create_user(
        username=username, email=f"{username}@example.com", password="secret123",
        role=User.Role.ADMIN,
        totp_secret=f"dummy-secret-{username}", totp_confirmed=True,
    )
    client.force_login(user)
    session = client.session
    session["2fa_verified"] = True
    session.save()
    return user


def _fy(entity, label="FY2026"):
    return FinancialYear.objects.create(
        entity=entity, year_label=label,
        start_date=date(2025, 7, 1), end_date=date(2026, 6, 30),
    )


def _give_distributable_income(fy, amount):
    """Same helper as core/tests_unit_trust_tax_planning.py: Section 1 is
    always recalculated from the TB on every tab load, so a worksheet's
    distributable_income has to come from a real TB line."""
    EntityChartOfAccount.objects.create(
        entity=fy.entity, account_code="400", account_name="Sales",
        section=EntityChartOfAccount.StatementSection.REVENUE,
    )
    TrialBalanceLine.objects.create(
        financial_year=fy, account_code="400", account_name="Sales",
        debit=Decimal("0.00"), credit=amount, closing_balance=amount,
    )


def _holder(entity, name, units, display_order=1):
    return EntityOfficer.objects.create(
        entity=entity, full_name=name,
        role=EntityOfficer.OfficerRole.UNIT_HOLDER, roles=["unit_holder"],
        units_held=units, display_order=display_order,
    )


# ---------------------------------------------------------------------------
# C2 -- a unit trust must still be able to SAVE a scenario
# ---------------------------------------------------------------------------
class UnitTrustCanObtainAScenarioTests(TestCase):
    def setUp(self):
        self.user = _logged_in_admin(self.client, "c2save")
        self.entity = Entity.objects.create(
            entity_name="Minli Enterprise Unit Trust", entity_type="trust_unit",
            assigned_accountant=self.user,
        )
        self.fy = _fy(self.entity)
        _give_distributable_income(self.fy, Decimal("100000.00"))
        _holder(self.entity, "A Pty Ltd", 50, display_order=1)
        _holder(self.entity, "B Pty Ltd", 50, display_order=2)
        EntityOfficer.recalculate_unit_percentages(self.entity)

    def _tab(self):
        return self.client.get(
            reverse("core:tax_planning_tab", kwargs={"pk": self.fy.pk}), secure=True,
        )

    def test_the_scenario_save_control_renders_for_a_unit_trust(self):
        response = self._tab()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="btnSaveScenario"')
        self.assertContains(response, 'id="scenarioName"')

    def test_apply_and_compare_stay_hidden_for_a_unit_trust(self):
        TaxPlanningScenario.objects.create(
            financial_year=self.fy, scenario_name="Register split",
            distributions=[], total_tax=Decimal("0"),
            total_distributed=Decimal("100000.00"),
        )
        response = self._tab()
        self.assertEqual(response.status_code, 200)
        # The chip renders (so the user can see the scenario exists) ...
        self.assertContains(response, "Register split")
        # ... but the modelling controls do not. Matched on the buttons'
        # own title/heading text, not on their CSS classes, which also
        # appear in this template's JavaScript.
        self.assertNotContains(response, 'title="Apply this scenario"')
        self.assertNotContains(response, "Scenario Comparison")

    def test_a_discretionary_trust_still_gets_the_full_modelling_section(self):
        entity = Entity.objects.create(
            entity_name="Vincent Family Trust", entity_type="trust",
            assigned_accountant=self.user,
        )
        fy = _fy(entity)
        _give_distributable_income(fy, Decimal("100000.00"))
        EntityOfficer.objects.create(
            entity=entity, full_name="Ben One", role="beneficiary",
            roles=["beneficiary"], distribution_percentage=Decimal("100.00"),
        )
        TaxPlanningScenario.objects.create(
            financial_year=fy, scenario_name="Option A",
            distributions=[], total_tax=Decimal("0"),
            total_distributed=Decimal("100000.00"),
        )

        response = self.client.get(
            reverse("core:tax_planning_tab", kwargs={"pk": fy.pk}), secure=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="btnSaveScenario"')
        self.assertContains(response, 'title="Apply this scenario"')
        self.assertContains(response, "Scenario Comparison")
        self.assertContains(response, "Scenario Modelling")

    def test_saving_a_scenario_for_a_unit_trust_is_accepted(self):
        """The endpoint behind that button: without a saved scenario there
        is nothing for TrustWorkspace.selected_tax_scenario to point at,
        and every downstream product of the distribution is dead."""
        self._tab()  # derive proposed_distribution from the register
        rows = TaxPlanningBeneficiaryRow.objects.filter(
            worksheet__financial_year=self.fy
        )
        payload = {
            "scenario_name": "FY2026 Register Split",
            "distributions": [
                {
                    "beneficiary_id": str(row.beneficiary_id),
                    "beneficiary_type": row.beneficiary_type,
                    "proposed_distribution": str(row.proposed_distribution),
                }
                for row in rows
            ],
            "total_tax": "0",
            "total_distributed": "100000.00",
        }
        response = self.client.post(
            reverse("core:tax_planning_scenario_save", kwargs={"pk": self.fy.pk}),
            data=json.dumps(payload), content_type="application/json", secure=True,
        )
        self.assertEqual(response.status_code, 200)
        scenario = TaxPlanningScenario.objects.get(financial_year=self.fy)
        self.assertEqual(scenario.scenario_name, "FY2026 Register Split")
        self.assertEqual(
            sum(
                Decimal(str(entry["proposed_distribution"]))
                for entry in scenario.distributions
            ),
            Decimal("100000.00"),
        )


# ---------------------------------------------------------------------------
# C2 (second half) -- the allocations form has to RENDER for a unit trust
# ---------------------------------------------------------------------------
class UnitTrustDistributionFormRendersTests(TestCase):
    def setUp(self):
        self.user = _logged_in_admin(self.client, "c2form")

    def test_the_allocations_form_renders_for_a_unit_trust(self):
        entity = Entity.objects.create(
            entity_name="Minli Enterprise Unit Trust", entity_type="trust_unit",
            assigned_accountant=self.user,
        )
        fy = _fy(entity)
        _holder(entity, "A Pty Ltd", 75, display_order=1)
        _holder(entity, "B Pty Ltd", 25, display_order=2)
        EntityOfficer.recalculate_unit_percentages(entity)

        response = self.client.get(
            reverse("core:trust_distribution", kwargs={"pk": fy.pk}), secure=True,
        )
        self.assertEqual(response.status_code, 200)
        # The submit button lives INSIDE {% if beneficiaries %}: if the
        # holders are missing from that list the form does not exist and
        # Task 8's save path is unreachable from the UI.
        self.assertContains(response, "Save Allocations")
        self.assertContains(response, 'value="save_allocations"')
        self.assertContains(response, "A Pty Ltd")
        self.assertContains(response, "B Pty Ltd")
        self.assertNotContains(response, "No unit holders found")
        # Register-derived, so shown as text rather than as an editable
        # percentage the save path would silently discard.
        self.assertContains(response, "75.00%")
        self.assertNotContains(response, 'name="pct_')

    def test_a_ceased_holder_is_not_offered_an_allocation(self):
        entity = Entity.objects.create(
            entity_name="Half Ceased Unit Trust", entity_type="trust_unit",
            assigned_accountant=self.user,
        )
        fy = _fy(entity)
        gone = _holder(entity, "Gone Holder", 50, display_order=1)
        gone.date_ceased = date.today() - timedelta(days=1)
        gone.save()
        _holder(entity, "Staying Holder", 50, display_order=2)
        EntityOfficer.recalculate_unit_percentages(entity)

        response = self.client.get(
            reverse("core:trust_distribution", kwargs={"pk": fy.pk}), secure=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Staying Holder")
        self.assertNotContains(response, "Gone Holder")

    def test_a_discretionary_trust_still_renders_editable_percentages(self):
        entity = Entity.objects.create(
            entity_name="Vincent Family Trust", entity_type="trust",
            assigned_accountant=self.user,
        )
        fy = _fy(entity)
        officer = EntityOfficer.objects.create(
            entity=entity, full_name="Ben One", role="beneficiary",
            roles=["beneficiary"], distribution_percentage=Decimal("100.00"),
        )

        response = self.client.get(
            reverse("core:trust_distribution", kwargs={"pk": fy.pk}), secure=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Save Allocations")
        self.assertContains(response, f'name="pct_{officer.pk}"')
        self.assertContains(response, "Beneficiary Allocations")

    def test_a_ceased_beneficiary_still_renders_on_a_discretionary_trust(self):
        """Re-review FIX 1: including unit holders in this list must not
        drag EntityOfficer.active_register_q() onto a DISCRETIONARY trust.
        A beneficiary who ceased mid-year is still entitled to a share of
        THAT year's income; hiding their row (and its pct_<pk> input) while
        the badge, the footer total and the is_fully_allocated gate kept
        counting their existing allocation meant the visible rows no longer
        summed to the displayed total. active_register_q also compares to
        TODAY, not to the year being distributed."""
        entity = Entity.objects.create(
            entity_name="Vincent Family Trust", entity_type="trust",
            assigned_accountant=self.user,
        )
        fy = _fy(entity)
        active = EntityOfficer.objects.create(
            entity=entity, full_name="Active Ben", role="beneficiary",
            roles=["beneficiary"], distribution_percentage=Decimal("60.00"),
            display_order=1,
        )
        ceased = EntityOfficer.objects.create(
            entity=entity, full_name="Ceased Ben", role="beneficiary",
            roles=["beneficiary"], distribution_percentage=Decimal("40.00"),
            display_order=2, date_ceased=date.today() - timedelta(days=30),
        )
        # A "trustee who is also a beneficiary" was in the pre-branch list
        # too, via the roles JSON list rather than the role field.
        hybrid = EntityOfficer.objects.create(
            entity=entity, full_name="Trustee Also Ben", role="trustee",
            roles=["trustee", "beneficiary"], display_order=3,
        )

        response = self.client.get(
            reverse("core:trust_distribution", kwargs={"pk": fy.pk}), secure=True,
        )
        self.assertEqual(response.status_code, 200)
        for officer in (active, ceased, hybrid):
            self.assertContains(response, officer.full_name)
            self.assertContains(response, f'name="pct_{officer.pk}"')

    def test_a_unit_trusts_list_is_still_active_filtered(self):
        """The other side of the same fix: on a unit trust the active
        filter stays, matching allocate_unit_trust_distribution's own
        active set -- a ceased holder holds no units in the denominator,
        so offering them a row would misrepresent the register."""
        entity = Entity.objects.create(
            entity_name="Minli Enterprise Unit Trust", entity_type="trust_unit",
            assigned_accountant=self.user,
        )
        fy = _fy(entity)
        gone = _holder(entity, "Gone Holder", 50, display_order=1)
        gone.date_ceased = date.today() - timedelta(days=30)
        gone.save()
        _holder(entity, "Staying Holder", 50, display_order=2)
        EntityOfficer.recalculate_unit_percentages(entity)

        response = self.client.get(
            reverse("core:trust_distribution", kwargs={"pk": fy.pk}), secure=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Staying Holder")
        self.assertNotContains(response, "Gone Holder")

    def test_the_rendered_form_actually_posts_a_unit_trust_allocation(self):
        """End to end: render the form, then POST exactly what it
        contains (the action alone -- a unit trust posts no percentages)
        and confirm the register-derived allocation lands."""
        entity = Entity.objects.create(
            entity_name="Posting Unit Trust", entity_type="trust_unit",
            assigned_accountant=self.user,
        )
        fy = _fy(entity)
        _holder(entity, "A Pty Ltd", 75, display_order=1)
        _holder(entity, "B Pty Ltd", 25, display_order=2)
        EntityOfficer.recalculate_unit_percentages(entity)
        dist = TrustDistribution.objects.create(
            financial_year=fy, distributable_income=Decimal("100000.00"),
        )

        url = reverse("core:trust_distribution", kwargs={"pk": fy.pk})
        self.assertContains(self.client.get(url, secure=True), "Save Allocations")
        response = self.client.post(url, data={"action": "save_allocations"}, secure=True)
        self.assertEqual(response.status_code, 302)

        rows = dist.allocations.all()
        self.assertEqual(rows.count(), 2)
        self.assertEqual(
            rows.get(beneficiary__full_name="A Pty Ltd").total_distribution,
            Decimal("75000.00"),
        )
        self.assertEqual(
            sum(row.total_distribution for row in rows), Decimal("100000.00"),
        )


# ---------------------------------------------------------------------------
# I1 -- a unit holder on a DISCRETIONARY trust keeps its audit trail
# ---------------------------------------------------------------------------
class UnitHolderOnADiscretionaryTrustKeepsItsHistoryTests(TestCase):
    """``_update_distribution_history`` early-returned for every
    ``unit_holder``, on the assumption that
    ``recalculate_unit_percentages`` writes the history instead. But that
    recompute only ever runs under ``if entity.is_unit_trust``, and
    core/forms.py still OFFERS the Unit Holder role on a plain ``trust``.
    So on a discretionary trust the percentage saved and NO history row was
    written -- an audit trail the four production discretionary trusts had
    before this branch.
    """

    def test_history_is_written_for_a_unit_holder_on_a_plain_trust(self):
        entity = Entity.objects.create(
            entity_name="Vincent Family Trust", entity_type="trust",
        )
        holder = EntityOfficer.objects.create(
            entity=entity, full_name="Hybrid Holder",
            role=EntityOfficer.OfficerRole.UNIT_HOLDER, roles=["unit_holder"],
            distribution_percentage=Decimal("40.00"),
        )
        history = OfficerDistributionHistory.objects.filter(officer=holder)
        self.assertEqual(history.count(), 1)
        self.assertEqual(history.first().distribution_pct, Decimal("40.00"))

    def test_a_later_change_is_recorded_too(self):
        entity = Entity.objects.create(
            entity_name="Vincent Family Trust", entity_type="trust",
        )
        holder = EntityOfficer.objects.create(
            entity=entity, full_name="Hybrid Holder",
            role=EntityOfficer.OfficerRole.UNIT_HOLDER, roles=["unit_holder"],
            distribution_percentage=Decimal("40.00"),
        )
        holder.distribution_percentage = Decimal("60.00")
        holder.save()

        # _write_distribution_history amends the row that already starts
        # today rather than opening a second one for the same day (see its
        # `newest.effective_from == today` branch), so the audit trail
        # reads one period, at the percentage that ended up stored.
        history = OfficerDistributionHistory.objects.filter(officer=holder)
        self.assertEqual(history.count(), 1)
        self.assertEqual(history.first().distribution_pct, Decimal("60.00"))

    def test_a_unit_trusts_holder_still_has_no_per_save_history(self):
        """The other half of the guard, unchanged: on a UNIT trust
        recalculate_unit_percentages is the sole writer, so an individual
        save() must not book a percentage of its own (it would race
        entity.total_units)."""
        entity = Entity.objects.create(
            entity_name="Minli Enterprise Unit Trust", entity_type="trust_unit",
        )
        holder = EntityOfficer.objects.create(
            entity=entity, full_name="A Pty Ltd",
            role=EntityOfficer.OfficerRole.UNIT_HOLDER, roles=["unit_holder"],
            units_held=50, distribution_percentage=Decimal("99.99"),
        )
        self.assertEqual(
            OfficerDistributionHistory.objects.filter(officer=holder).count(), 0,
        )

        EntityOfficer.recalculate_unit_percentages(entity)

        holder.refresh_from_db()
        self.assertEqual(holder.distribution_percentage, Decimal("100.00"))
        history = OfficerDistributionHistory.objects.filter(officer=holder)
        self.assertEqual(history.count(), 1)
        self.assertEqual(history.first().distribution_pct, Decimal("100.00"))

    def test_a_beneficiary_on_a_unit_trust_still_gets_history(self):
        """The guard is qualified on entity type as well as role, so it
        must not swallow a beneficiary's history on a unit trust either."""
        entity = Entity.objects.create(
            entity_name="Minli Enterprise Unit Trust", entity_type="trust_unit",
        )
        ben = EntityOfficer.objects.create(
            entity=entity, full_name="Odd Beneficiary", role="beneficiary",
            roles=["beneficiary"], distribution_percentage=Decimal("10.00"),
        )
        self.assertEqual(
            OfficerDistributionHistory.objects.filter(officer=ben).count(), 1,
        )


# ---------------------------------------------------------------------------
# I2 -- a GET must not rewrite a finalised worksheet
# ---------------------------------------------------------------------------
class FinalisedWorksheetIsNotRewrittenOnGetTests(TestCase):
    def setUp(self):
        self.user = _logged_in_admin(self.client, "i2fin")
        self.entity = Entity.objects.create(
            entity_name="Minli Enterprise Unit Trust", entity_type="trust_unit",
            assigned_accountant=self.user,
        )
        self.fy = _fy(self.entity, label="FY2024")
        _give_distributable_income(self.fy, Decimal("100000.00"))
        self.a = _holder(self.entity, "A Pty Ltd", 50, display_order=1)
        self.b = _holder(self.entity, "B Pty Ltd", 50, display_order=2)
        EntityOfficer.recalculate_unit_percentages(self.entity)

    def _tab(self):
        return self.client.get(
            reverse("core:tax_planning_tab", kwargs={"pk": self.fy.pk}), secure=True,
        )

    def test_a_finalised_worksheet_keeps_the_split_it_was_finalised_with(self):
        self.assertEqual(self._tab().status_code, 200)
        worksheet = TaxPlanningWorksheet.objects.get(financial_year=self.fy)
        self.assertEqual(
            sorted(
                worksheet.beneficiary_rows.values_list(
                    "proposed_distribution", flat=True
                )
            ),
            [Decimal("50000.00"), Decimal("50000.00")],
        )

        worksheet.status = TaxPlanningWorksheet.WorksheetStatus.FINALISED
        worksheet.save(update_fields=["status"])
        self.assertTrue(worksheet.is_finalised)

        # The register moves on AFTER the year was finalised -- exactly
        # what happens when units change hands in a later year.
        self.b.units_held = 150
        self.b.save(update_fields=["units_held"])
        EntityOfficer.recalculate_unit_percentages(self.entity)

        self.assertEqual(self._tab().status_code, 200)

        self.assertEqual(
            sorted(
                worksheet.beneficiary_rows.values_list(
                    "proposed_distribution", flat=True
                )
            ),
            [Decimal("50000.00"), Decimal("50000.00")],
            "opening a finalised worksheet re-derived it from TODAY's register",
        )

    def test_an_open_worksheet_is_still_derived_on_every_get(self):
        self.assertEqual(self._tab().status_code, 200)
        self.b.units_held = 150
        self.b.save(update_fields=["units_held"])
        EntityOfficer.recalculate_unit_percentages(self.entity)

        self.assertEqual(self._tab().status_code, 200)
        worksheet = TaxPlanningWorksheet.objects.get(financial_year=self.fy)
        self.assertEqual(
            sorted(
                worksheet.beneficiary_rows.values_list(
                    "proposed_distribution", flat=True
                )
            ),
            [Decimal("25000.00"), Decimal("75000.00")],
        )


# ---------------------------------------------------------------------------
# I3 -- the odd cent follows the displayed percentage
# ---------------------------------------------------------------------------
class OddCentFollowsTheDisplayedPercentageTests(TestCase):
    """Three tie-breaks decided who gets the odd fraction, and they did not
    agree: ``recalculate_unit_percentages`` sorted on the officer's random
    UUID pk, ``allocate_by_units`` on ``str(key)``, and the tax planning
    tab supplied a THIRD key (the worksheet row's own pk). With three equal
    holders that let the holder displayed at 33.34% be a different holder
    from the one receiving $33,333.34.
    """

    def setUp(self):
        self.user = _logged_in_admin(self.client, "i3tie")
        self.entity = Entity.objects.create(
            entity_name="Threeway Unit Trust", entity_type="trust_unit",
            assigned_accountant=self.user,
        )
        self.fy = _fy(self.entity)
        _give_distributable_income(self.fy, Decimal("100000.00"))
        for order, name in enumerate(["Zed Holder", "Mid Holder", "Ann Holder"], 1):
            _holder(self.entity, name, 1, display_order=order)
        EntityOfficer.recalculate_unit_percentages(self.entity)

    def _largest_percentage_holder(self):
        holders = list(self.entity.officers.all())
        top = max(h.distribution_percentage for h in holders)
        names = [h.full_name for h in holders if h.distribution_percentage == top]
        self.assertEqual(len(names), 1, "expected exactly one holder at 33.34%")
        return names[0], top

    def test_the_journal_allocation_agrees_with_the_displayed_percentage(self):
        from core.views_trust import allocate_unit_trust_distribution

        name, top = self._largest_percentage_holder()
        self.assertEqual(top, Decimal("33.34"))

        dist = TrustDistribution.objects.create(
            financial_year=self.fy, distributable_income=Decimal("100000.00"),
            other_income=Decimal("100000.00"),
        )
        allocate_unit_trust_distribution(dist)

        rows = list(dist.allocations.all())
        self.assertEqual(
            sum(row.total_distribution for row in rows), Decimal("100000.00"),
        )
        top_amount = max(row.total_distribution for row in rows)
        self.assertEqual(top_amount, Decimal("33333.34"))
        winners = [
            row.beneficiary.full_name for row in rows
            if row.total_distribution == top_amount
        ]
        self.assertEqual(
            winners, [name],
            "the holder displayed the larger percentage must also receive "
            "the larger amount",
        )

    def test_the_tax_planning_tab_agrees_with_the_displayed_percentage(self):
        name, _ = self._largest_percentage_holder()

        response = self.client.get(
            reverse("core:tax_planning_tab", kwargs={"pk": self.fy.pk}), secure=True,
        )
        self.assertEqual(response.status_code, 200)

        rows = list(
            TaxPlanningBeneficiaryRow.objects.filter(
                worksheet__financial_year=self.fy
            ).select_related("beneficiary")
        )
        self.assertEqual(
            sum(row.proposed_distribution for row in rows), Decimal("100000.00"),
        )
        top_amount = max(row.proposed_distribution for row in rows)
        self.assertEqual(top_amount, Decimal("33333.34"))
        winners = [
            row.beneficiary.full_name for row in rows
            if row.proposed_distribution == top_amount
        ]
        self.assertEqual(winners, [name])

    def test_percentage_and_money_tie_breaks_agree_on_the_same_holder(self):
        """The two paths above, compared directly to each other rather
        than each to a constant."""
        from core.unit_allocation import allocate_by_units

        holders = list(self.entity.officers.order_by("display_order", "full_name"))
        holdings = [
            ((h.display_order, h.full_name, h.pk), h.units_held) for h in holders
        ]
        split = allocate_by_units(Decimal("100000.00"), holdings)

        money_winner = max(split, key=lambda key: split[key])[1]
        percentage_winner, _ = self._largest_percentage_holder()
        self.assertEqual(money_winner, percentage_winner)


# ---------------------------------------------------------------------------
# I5 -- the ceased-holder message must be true, and use the right noun
# ---------------------------------------------------------------------------
class CeasedHolderMessageTests(TestCase):
    def setUp(self):
        self.user = _logged_in_admin(self.client, "i5msg")

    def _cease(self, officer, roles, units=""):
        return self.client.post(
            reverse("core:entity_officer_edit", args=[officer.pk]),
            data={
                "full_name": officer.full_name,
                "roles_multi": roles,
                "title": "",
                "date_appointed": "",
                "date_ceased": (date.today() - timedelta(days=1)).isoformat(),
                "display_order": str(officer.display_order),
                "profit_share_percentage": "",
                "distribution_percentage": (
                    "" if units != "" else str(officer.distribution_percentage or "")
                ),
                "units_held": str(units),
            },
            secure=True,
        )

    def _messages(self, response):
        return [str(m) for m in get_messages(response.wsgi_request)]

    def test_two_survivors_on_a_unit_trust_are_told_the_truth(self):
        entity = Entity.objects.create(
            entity_name="Minli Enterprise Unit Trust", entity_type="trust_unit",
        )
        a = _holder(entity, "A Pty Ltd", 50, display_order=1)
        _holder(entity, "B Pty Ltd", 25, display_order=2)
        _holder(entity, "C Pty Ltd", 25, display_order=3)
        EntityOfficer.recalculate_unit_percentages(entity)

        response = self._cease(a, ["unit_holder"], units=50)
        self.assertEqual(response.status_code, 302)
        text = " ".join(self._messages(response))

        # The recompute above already reallocated: saying otherwise, on a
        # screen where distribution_percentage is disabled, was both false
        # and impossible to act on.
        self.assertNotIn("has not been reallocated", text)
        self.assertNotIn("Please update distribution percentages manually", text)
        self.assertIn("reallocated automatically", text)
        self.assertIn("unit holders", text)

        survivors = entity.officers.exclude(pk=a.pk)
        self.assertEqual(
            sum(s.distribution_percentage for s in survivors), Decimal("100.00"),
        )

    def test_a_discretionary_trust_is_told_about_beneficiaries(self):
        entity = Entity.objects.create(
            entity_name="Vincent Family Trust", entity_type="trust",
        )
        one = EntityOfficer.objects.create(
            entity=entity, full_name="Ben One", role="beneficiary",
            roles=["beneficiary"], distribution_percentage=Decimal("40.00"),
            display_order=1,
        )
        for name, pct in [("Ben Two", Decimal("30.00")), ("Ben Three", Decimal("30.00"))]:
            EntityOfficer.objects.create(
                entity=entity, full_name=name, role="beneficiary",
                roles=["beneficiary"], distribution_percentage=pct,
            )

        response = self._cease(one, ["beneficiary"])
        self.assertEqual(response.status_code, 302)
        text = " ".join(self._messages(response))

        # Unchanged behaviour for a discretionary trust -- except the noun,
        # which used to say "unit holders" to every trust on the platform.
        self.assertIn("has not been reallocated", text)
        self.assertIn("beneficiaries", text)
        self.assertNotIn("unit holder", text)

    def test_the_sole_survivor_message_uses_the_right_noun(self):
        entity = Entity.objects.create(
            entity_name="Vincent Family Trust", entity_type="trust",
        )
        one = EntityOfficer.objects.create(
            entity=entity, full_name="Ben One", role="beneficiary",
            roles=["beneficiary"], distribution_percentage=Decimal("40.00"),
            display_order=1,
        )
        EntityOfficer.objects.create(
            entity=entity, full_name="Ben Two", role="beneficiary",
            roles=["beneficiary"], distribution_percentage=Decimal("60.00"),
        )

        response = self._cease(one, ["beneficiary"])
        self.assertEqual(response.status_code, 302)
        text = " ".join(self._messages(response))
        self.assertIn("sole active beneficiary", text)
        self.assertNotIn("sole active unit holder", text)
