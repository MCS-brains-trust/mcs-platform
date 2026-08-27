"""The tab opens for a unit trust, and its distribution follows the register.

setUp assigns each entity to the test user (assigned_accountant=self.user):
the default User.role is "accountant" (accounts/models.py), and
tax_planning_tab denies an accountant access to any entity that is not
their own (core/views_tax_planning.py) BEFORE the entity-type gate ever
runs -- get_financial_year_for_user (config/authorization.py) raises
PermissionDenied first if the entity is unassigned. Omitting this would
make every test fail for an ownership reason unrelated to this task's bug.

Each unit-trust FY also carries one revenue-tagged trial balance line, so
that Section 1's TB-driven recalculation (which tax_planning_tab always
runs and always persists, overwriting whatever distributable_income a test
might otherwise set directly on the worksheet) produces a known,
non-zero distributable_income for allocate_by_units to split.
"""
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.models import (
    Entity,
    EntityChartOfAccount,
    EntityOfficer,
    FinancialYear,
    TaxPlanningBeneficiaryRow,
    TaxPlanningWorksheet,
    TrialBalanceLine,
)


def _give_distributable_income(fy, amount):
    """Tag one revenue account and post a TB line so Section 1's
    calculate_section1_from_tb(fy) resolves distributable_income to `amount`.
    """
    EntityChartOfAccount.objects.create(
        entity=fy.entity, account_code="400", account_name="Sales",
        section=EntityChartOfAccount.StatementSection.REVENUE,
    )
    TrialBalanceLine.objects.create(
        financial_year=fy, account_code="400", account_name="Sales",
        debit=Decimal("0.00"), credit=amount, closing_balance=amount,
    )


class UnitTrustTaxPlanningTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="tp", email="tp@example.com", password="secret123",
            totp_secret="dummy-secret-for-test", totp_confirmed=True,
        )
        self.client.force_login(self.user)
        session = self.client.session
        session["2fa_verified"] = True
        session.save()

        self.entity = Entity.objects.create(
            entity_name="Minli Enterprise Unit Trust", entity_type="trust_unit",
            assigned_accountant=self.user,
        )
        self.fy = FinancialYear.objects.create(
            entity=self.entity, year_label="FY2026",
            start_date=date(2025, 7, 1), end_date=date(2026, 6, 30),
        )
        _give_distributable_income(self.fy, Decimal("100000.00"))
        for name, units in [("A Pty Ltd", 50), ("B Pty Ltd", 50)]:
            EntityOfficer.objects.create(
                entity=self.entity, full_name=name,
                role=EntityOfficer.OfficerRole.UNIT_HOLDER,
                roles=["unit_holder"], units_held=units,
            )

    def test_tab_opens_for_a_unit_trust(self):
        response = self.client.get(
            reverse("core:tax_planning_tab", kwargs={"pk": self.fy.pk}),
            secure=True,
        )
        self.assertEqual(response.status_code, 200)

    def test_a_row_is_created_for_every_unit_holder(self):
        self.client.get(
            reverse("core:tax_planning_tab", kwargs={"pk": self.fy.pk}),
            secure=True,
        )
        worksheet = TaxPlanningWorksheet.objects.get(financial_year=self.fy)
        self.assertEqual(worksheet.beneficiary_rows.count(), 2)

    def test_proposed_distribution_is_derived_from_units(self):
        response = self.client.get(
            reverse("core:tax_planning_tab", kwargs={"pk": self.fy.pk}),
            secure=True,
        )
        self.assertEqual(response.status_code, 200)
        worksheet = TaxPlanningWorksheet.objects.get(financial_year=self.fy)
        # Section 1 recalculation from the TB line makes this exact.
        self.assertEqual(worksheet.distributable_income, Decimal("100000.00"))
        amounts = sorted(
            worksheet.beneficiary_rows.values_list("proposed_distribution", flat=True)
        )
        self.assertEqual(amounts, [Decimal("50000.00"), Decimal("50000.00")])

    def test_a_posted_distribution_override_is_rejected(self):
        # There is no planning: the register decides. Load the tab first so
        # the worksheet exists and its rows carry register-derived amounts.
        self.client.get(
            reverse("core:tax_planning_tab", kwargs={"pk": self.fy.pk}), secure=True,
        )
        worksheet = TaxPlanningWorksheet.objects.get(financial_year=self.fy)
        self.assertFalse(worksheet.is_finalised)
        row = worksheet.beneficiary_rows.first()
        self.assertEqual(row.proposed_distribution, Decimal("50000.00"))

        # Real JSON, against a non-finalised worksheet, per Rulings 1 and 2:
        # tax_planning_save is a JSON API (json.loads(request.body)) that
        # 400s on invalid JSON and on a finalised worksheet.
        response = self.client.post(
            reverse("core:tax_planning_save", kwargs={"pk": self.fy.pk}),
            data={
                "beneficiary_rows": [{
                    "beneficiary_id": str(row.beneficiary_id),
                    "outside_income": "5000.00",
                    "proposed_distribution": "90000.00",
                    "beneficiary_type": row.beneficiary_type,
                }],
            },
            content_type="application/json",
            secure=True,
        )
        self.assertEqual(response.status_code, 200)

        row.refresh_from_db()
        # The override is rejected: the register-derived amount survives.
        self.assertEqual(row.proposed_distribution, Decimal("50000.00"))
        # outside_income describes the holder's own tax position, not the
        # trust's allocation -- it is still accepted (Ruling 4).
        self.assertEqual(row.outside_income, Decimal("5000.00"))

    def test_null_units_held_does_not_crash_the_split(self):
        # A unit_holder row with units_held left blank must not blow up
        # allocate_by_units with a bare TypeError (units_held is nullable).
        EntityOfficer.objects.create(
            entity=self.entity, full_name="C Pty Ltd (no units yet)",
            role=EntityOfficer.OfficerRole.UNIT_HOLDER,
            roles=["unit_holder"], units_held=None,
        )
        response = self.client.get(
            reverse("core:tax_planning_tab", kwargs={"pk": self.fy.pk}),
            secure=True,
        )
        self.assertEqual(response.status_code, 200)
        worksheet = TaxPlanningWorksheet.objects.get(financial_year=self.fy)
        self.assertEqual(worksheet.beneficiary_rows.count(), 3)
        null_row = worksheet.beneficiary_rows.get(beneficiary__full_name__contains="no units yet")
        self.assertEqual(null_row.proposed_distribution, Decimal("0.00"))
        # The other two still split the whole $100,000 between them.
        others = sorted(
            worksheet.beneficiary_rows.exclude(pk=null_row.pk)
            .values_list("proposed_distribution", flat=True)
        )
        self.assertEqual(others, [Decimal("50000.00"), Decimal("50000.00")])


class EmptyUnitRegisterTests(TestCase):
    """An empty (or unit-less) register must not 500 the tab."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="tp2", email="tp2@example.com", password="secret123",
            totp_secret="dummy-secret-for-test", totp_confirmed=True,
        )
        self.client.force_login(self.user)
        session = self.client.session
        session["2fa_verified"] = True
        session.save()

        self.entity = Entity.objects.create(
            entity_name="Empty Register Unit Trust", entity_type="trust_unit",
            assigned_accountant=self.user,
        )
        self.fy = FinancialYear.objects.create(
            entity=self.entity, year_label="FY2026",
            start_date=date(2025, 7, 1), end_date=date(2026, 6, 30),
        )
        _give_distributable_income(self.fy, Decimal("100000.00"))
        # Deliberately no EntityOfficer rows at all.

    def test_empty_register_renders_with_a_warning_instead_of_500(self):
        response = self.client.get(
            reverse("core:tax_planning_tab", kwargs={"pk": self.fy.pk}),
            secure=True,
        )
        self.assertEqual(response.status_code, 200)
        worksheet = TaxPlanningWorksheet.objects.get(financial_year=self.fy)
        self.assertEqual(worksheet.beneficiary_rows.count(), 0)
        messages = list(response.context["messages"])
        self.assertTrue(
            any("register" in str(m).lower() for m in messages),
            f"expected a register warning message, got: {[str(m) for m in messages]}",
        )


class DiscretionaryTrustTaxPlanningUnchangedTests(TestCase):
    """The tab must behave exactly as before for a discretionary trust."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="tp3", email="tp3@example.com", password="secret123",
            totp_secret="dummy-secret-for-test", totp_confirmed=True,
        )
        self.client.force_login(self.user)
        session = self.client.session
        session["2fa_verified"] = True
        session.save()

        self.entity = Entity.objects.create(
            entity_name="Old Faithful Family Trust", entity_type="trust",
            assigned_accountant=self.user,
        )
        self.fy = FinancialYear.objects.create(
            entity=self.entity, year_label="FY2026",
            start_date=date(2025, 7, 1), end_date=date(2026, 6, 30),
        )
        _give_distributable_income(self.fy, Decimal("100000.00"))
        self.beneficiary = EntityOfficer.objects.create(
            entity=self.entity, full_name="Jane Beneficiary",
            role=EntityOfficer.OfficerRole.BENEFICIARY,
            roles=["beneficiary"],
        )

    def test_manual_distribution_is_still_accepted(self):
        # Build a discretionary trust with a beneficiary, post an allocation,
        # and assert it saves — the behaviour unit trusts are giving up.
        self.client.get(
            reverse("core:tax_planning_tab", kwargs={"pk": self.fy.pk}), secure=True,
        )
        worksheet = TaxPlanningWorksheet.objects.get(financial_year=self.fy)
        self.assertFalse(worksheet.is_finalised)
        row = TaxPlanningBeneficiaryRow.objects.get(
            worksheet=worksheet, beneficiary=self.beneficiary,
        )
        # Starts at the row default -- nothing has derived a value for a
        # discretionary trust, unlike a unit trust.
        self.assertEqual(row.proposed_distribution, Decimal("0.00"))

        response = self.client.post(
            reverse("core:tax_planning_save", kwargs={"pk": self.fy.pk}),
            data={
                "beneficiary_rows": [{
                    "beneficiary_id": str(row.beneficiary_id),
                    "outside_income": "12000.00",
                    "proposed_distribution": "75000.00",
                    "beneficiary_type": "individual",
                }],
            },
            content_type="application/json",
            secure=True,
        )
        self.assertEqual(response.status_code, 200)

        row.refresh_from_db()
        # Unlike a unit trust, a discretionary trust's manual proposal is a
        # real decision and must persist exactly as posted.
        self.assertEqual(row.proposed_distribution, Decimal("75000.00"))
        self.assertEqual(row.outside_income, Decimal("12000.00"))
