"""
Regression test for the financial-year detail tab counts.

The Trial Balance and Activity tabs rendered as "Trial Balance ()" because
the view hands the template a Python list, so `{{ tb_lines.count }}` resolves
to the bound `list.count` method (which Django renders as an empty string)
rather than the number of rows.
"""

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.models import Entity, FinancialYear, TrialBalanceLine


class FinancialYearDetailTabCountTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="tabs",
            email="tabs@example.com",
            password="secret123",
            role="admin",
            totp_secret="dummy-secret-for-test",
            totp_confirmed=True,
        )
        self.entity = Entity.objects.create(entity_name="Berwick Mechanical")
        self.fy = FinancialYear.objects.create(
            entity=self.entity,
            start_date=date(2024, 7, 1),
            end_date=date(2025, 6, 30),
        )
        for code, name, debit in (
            ("2000", "Cash at bank", Decimal("100.00")),
            ("630", "Sales", Decimal("0.00")),
            ("1965", "Wages", Decimal("50.00")),
        ):
            TrialBalanceLine.objects.create(
                financial_year=self.fy,
                account_code=code,
                account_name=name,
                debit=debit,
                credit=Decimal("0.00"),
            )

    def test_trial_balance_tab_shows_the_line_count(self):
        self.client.force_login(self.user)
        session = self.client.session
        session["2fa_verified"] = True
        session.save()

        response = self.client.get(
            reverse("core:financial_year_detail", kwargs={"pk": self.fy.pk}),
            secure=True,
        )

        self.assertContains(response, "Trial Balance (3)")

    def test_activity_tab_renders_a_numeric_count(self):
        self.client.force_login(self.user)
        session = self.client.session
        session["2fa_verified"] = True
        session.save()

        response = self.client.get(
            reverse("core:financial_year_detail", kwargs={"pk": self.fy.pk}),
            secure=True,
        )

        self.assertNotContains(response, "Activity ()")
