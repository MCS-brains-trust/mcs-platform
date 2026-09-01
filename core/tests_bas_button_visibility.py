"""Who gets the GST / BAS button on the financial-year page.

The button used to be gated on `has_bank_statements`, which encoded the
assumption that GST could only arrive from a bank statement. Cashbook
journals now carry their own GST, so a GST-registered entity with no bank
feed at all (Elliott Jaques) still has a BAS to lodge. The gate is
registration, not provenance.
"""

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.models import Entity, FinancialYear, TrialBalanceLine
from core.test_support import Require2FAMixin


class BasButtonVisibilityTest(Require2FAMixin, TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="acct", email="acct@example.com", password="pw",
            role="accountant", totp_secret="TESTSECRET", totp_confirmed=True,
        )
        self.login_as(self.user)

    def _year(self, *, gst_registered, with_bank_statement=False):
        entity = Entity.objects.create(
            entity_name="Client %s" % Entity.objects.count(),
            entity_type=Entity.EntityType.SOLE_TRADER,
            is_gst_registered=gst_registered,
            assigned_accountant=self.user,
        )
        fy = FinancialYear.objects.create(
            entity=entity, year_label="Q2 2026",
            start_date=date(2025, 10, 1), end_date=date(2025, 12, 31),
        )
        if with_bank_statement:
            TrialBalanceLine.objects.create(
                financial_year=fy, account_code="1100",
                account_name="Business bank account",
                debit=Decimal("100.00"), credit=Decimal("0"),
                closing_balance=Decimal("100.00"), source="bank_statement",
            )
        return fy

    def _get(self, fy):
        return self.client.get(
            reverse("core:financial_year_detail", args=[fy.pk]), secure=True,
        )

    def _bas_href(self, fy):
        return reverse("core:gst_activity_statement", args=[fy.pk])

    # ---- the case the cashbook work created -----------------------------

    def test_gst_registered_year_with_no_bank_statements_gets_the_button(self):
        """Elliott Jaques' shape: GST comes from a cashbook journal, and the
        year has no bank feed at all."""
        fy = self._year(gst_registered=True)
        response = self._get(fy)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["can_lodge_bas"])
        self.assertIn(self._bas_href(fy), response.content.decode())

    # ---- the cases that must not change ---------------------------------

    def test_a_bank_statement_year_still_gets_the_button(self):
        fy = self._year(gst_registered=True, with_bank_statement=True)
        response = self._get(fy)
        self.assertTrue(response.context["can_lodge_bas"])
        self.assertIn(self._bas_href(fy), response.content.decode())

    def test_a_non_registered_entity_does_not_get_the_button(self):
        fy = self._year(gst_registered=False)
        response = self._get(fy)
        self.assertFalse(response.context["can_lodge_bas"])
        html = response.content.decode()
        self.assertNotIn(self._bas_href(fy), html)
        self.assertIn("GST/BAS is available for GST-registered entities", html)

    def test_has_bank_statements_still_means_bank_statements(self):
        """The opening-balance branch reads it, so widening the button must
        not widen this."""
        registered_no_bank = self._year(gst_registered=True)
        self.assertFalse(self._get(registered_no_bank).context["has_bank_statements"])

        with_bank = self._year(gst_registered=True, with_bank_statement=True)
        self.assertTrue(self._get(with_bank).context["has_bank_statements"])
