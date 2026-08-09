"""
Regression tests for the AI prompt context builders.

_build_entity_context and _calculate_materiality were written against a
TrialBalanceLine shape that does not exist: `section`, `adjusted_balance`,
`original_balance` and `prior_year_balance` are none of them fields, properties or
attributes of the model. _build_entity_context's order_by("section", ...) raises
FieldError on the second statement of the function, so every caller —
analyse_risk_flag, prioritise_flags, generate_risk_summary_report — was dead, and in
financial_year_finalise_full the failure is swallowed by a log-only try/except so
finalisation reported success while AI analysis had never once run.

These tests pin the real model fields the builders must read.
"""

from datetime import date
from decimal import Decimal

from django.test import TestCase, override_settings

from accounts.models import User
from core.ai_service import _build_entity_context, _calculate_materiality
from core.models import (
    Client as ClientModel,
    Entity,
    FinancialYear,
    TrialBalanceLine,
)

STORAGES_OVERRIDE = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@override_settings(STORAGES=STORAGES_OVERRIDE)
class EntityContextTests(TestCase):
    """
    Account codes follow the HandiLedger ranges core/views_audit.py's
    _section_for_code classifies: 0-999 revenue, 1000-1999 expenses,
    2000-2999 assets, 3000-3999 liabilities. Balances are debit-positive,
    the convention TrialBalanceLine.closing_balance already uses.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="ai_ctx_test_admin",
            password="testpass123",
            role=User.Role.ADMIN,
            totp_secret="dummy-secret-ai-ctx",
            totp_confirmed=True,
        )
        cls.client_obj = ClientModel.objects.create(name="AI Context Test Client")
        cls.entity = Entity.objects.create(
            entity_name="AI Context Test Co Pty Ltd",
            entity_type="company",
            client=cls.client_obj,
            primary_accountant=cls.user,
        )
        cls.fy = FinancialYear.objects.create(
            entity=cls.entity,
            year_label="FY2025",
            start_date=date(2024, 7, 1),
            end_date=date(2025, 6, 30),
            status=FinancialYear.Status.DRAFT,
        )
        # Revenue 400,000 (credit balance), expenses 120,000, assets 250,000
        for code, name, closing, prior in [
            ("0570", "Sales", Decimal("-400000.00"), Decimal("-350000.00")),
            ("1617", "Depreciation – Other", Decimal("120000.00"), Decimal("100000.00")),
            ("2000", "Cash at Bank", Decimal("250000.00"), Decimal("200000.00")),
            ("3000", "Trade Creditors", Decimal("-90000.00"), Decimal("-80000.00")),
        ]:
            TrialBalanceLine.objects.create(
                financial_year=cls.fy,
                account_code=code,
                account_name=name,
                closing_balance=closing,
                prior_closing_balance=prior,
                debit=closing if closing > 0 else Decimal("0"),
                credit=-closing if closing < 0 else Decimal("0"),
                source="tb_import",
            )

    def test_materiality_is_calculated_from_revenue(self):
        """
        Revenue of 400,000 is under the 500,000 small-entity threshold, so the
        rate is 2%: overall 8,000, performance 6,000, trivial 400.
        """
        mat = _calculate_materiality(self.fy)

        self.assertEqual(mat["base_type"], "revenue")
        self.assertEqual(mat["base_amount"], Decimal("400000.00"))
        self.assertEqual(mat["overall_materiality"], Decimal("8000"))
        self.assertEqual(mat["performance_materiality"], Decimal("6000"))
        self.assertEqual(mat["trivial_threshold"], Decimal("400"))

    def test_entity_context_lists_every_trial_balance_line(self):
        """Each line reaches the prompt with its code, name and current balance."""
        ctx = _build_entity_context(self.fy, include_materiality=False)

        for code, name in [
            ("0570", "Sales"),
            ("1617", "Depreciation – Other"),
            ("2000", "Cash at Bank"),
            ("3000", "Trade Creditors"),
        ]:
            self.assertIn(code, ctx)
            self.assertIn(name, ctx)

    def test_entity_context_classifies_lines_into_sections(self):
        """
        The section drives the summary totals, so an unclassified line is a
        line the AI never sees the significance of.
        """
        ctx = _build_entity_context(self.fy, include_materiality=False)

        self.assertNotIn("Section: None", ctx)
        self.assertNotIn("Unclassified", ctx)
        self.assertIn("Revenue=$400,000.00", ctx)
        self.assertIn("Expenses=$120,000.00", ctx)
        self.assertIn("Assets=$250,000.00", ctx)
        self.assertIn("Liabilities=$90,000.00", ctx)

    def test_entity_context_reports_prior_year_comparatives(self):
        """prior_closing_balance is the comparative the variance is measured against."""
        ctx = _build_entity_context(self.fy, include_materiality=False)

        self.assertIn("Prior: $200,000.00", ctx)
        self.assertIn("Var: $50,000.00", ctx)

    def test_entity_context_includes_materiality_when_asked(self):
        """The include_materiality path runs _calculate_materiality inline."""
        ctx = _build_entity_context(self.fy, include_materiality=True)

        self.assertIn("=== MATERIALITY ===", ctx)
        self.assertIn("$8,000", ctx)

    def test_entity_context_survives_a_year_with_no_trial_balance(self):
        """A year with nothing imported yet must not raise."""
        empty_fy = FinancialYear.objects.create(
            entity=self.entity,
            year_label="FY2026",
            start_date=date(2025, 7, 1),
            end_date=date(2026, 6, 30),
            status=FinancialYear.Status.DRAFT,
        )

        ctx = _build_entity_context(empty_fy, include_materiality=True)
        self.assertIn("=== TRIAL BALANCE ===", ctx)
