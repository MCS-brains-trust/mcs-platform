"""Regression test for core/fs_template_service._build_beneficiary_distribution_summary.

Defect (Dr Services Family Trust FY2026): the loan reconciliation on page 2 of
the Beneficiaries Profit Distribution Summary reported a closing balance of
75,194 against a Balance Sheet beneficiary loan of 107,898 -- understated by
exactly the 32,704 brought-forward balance.

`_figures_for_year` summed ``closing`` from non-rollover trial balance lines
only, while taking ``opening`` from the rollover lines' prior_credit. So the
b/f balance was counted in the opening row but dropped from the closing row.
``_resolve`` then back-solves "Funds loaned to trust" from that closing, so the
error propagated into the movement line with the opposite sign and the table
still footed while both figures were wrong.

The invariant asserted here: the summary's closing balance equals the sum of
every trial balance line on the beneficiary's mapped accounts -- which is what
the Balance Sheet reports as the beneficiary loan.
"""
from datetime import date
from decimal import Decimal

from django.test import TestCase, override_settings

from core.fs_template_service import _build_beneficiary_distribution_summary
from core.models import (
    Client, ClientAccountMapping, Entity, EntityOfficer, FinancialYear,
    TrialBalanceLine,
)

STORAGES_OVERRIDE = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


def _pdf_text(buffer):
    from pypdf import PdfReader

    buffer.seek(0)
    return "\n".join(page.extract_text() or "" for page in PdfReader(buffer).pages)


@override_settings(STORAGES=STORAGES_OVERRIDE)
class BeneficiarySummaryRolloverClosingTests(TestCase):
    """Mirrors the Dr Services FY2026 account shape.

    4000.01  rollover        b/f loan balance consolidated by the roll-forward
    4004.01  manual_journal  further funds loaned by the beneficiary
    4053.01  manual_journal  physical distribution paid out
    """

    @classmethod
    def setUpTestData(cls):
        cls.client_obj = Client.objects.create(name="Rollover Closing Test Client")
        cls.entity = Entity.objects.create(
            entity_name="Rollover Closing Family Trust",
            entity_type="trust",
            client=cls.client_obj,
        )
        cls.officer = EntityOfficer.objects.create(
            entity=cls.entity,
            full_name="Ronen Davidov",
            role="beneficiary",
            display_order=1,
        )
        cls.prior_fy = FinancialYear.objects.create(
            entity=cls.entity,
            year_label="2025",
            start_date=date(2024, 7, 1),
            end_date=date(2025, 6, 30),
        )
        cls.fy = FinancialYear.objects.create(
            entity=cls.entity,
            year_label="2026",
            start_date=date(2025, 7, 1),
            end_date=date(2026, 6, 30),
            prior_year=cls.prior_fy,
        )
        for code, name in (
            ("4000.01", "Opening balance - Beneficiary"),
            ("4004.01", "Funds loaned to trust"),
            ("4053.01", "Physical distribution"),
        ):
            ClientAccountMapping.objects.create(
                entity=cls.entity,
                client_account_code=code,
                client_account_name=name,
                beneficiary_officer=cls.officer,
            )

        # Brought forward: trust owes the beneficiary 32,703.73 (credit).
        TrialBalanceLine.objects.create(
            financial_year=cls.fy,
            account_code="4000.01",
            account_name="Opening balance - Beneficiary",
            closing_balance=Decimal("-32703.73"),
            prior_credit=Decimal("32703.73"),
            source="rollover",
        )
        # Current-year movements.
        TrialBalanceLine.objects.create(
            financial_year=cls.fy,
            account_code="4004.01",
            account_name="Funds loaned to trust",
            closing_balance=Decimal("-24049.02"),
            credit=Decimal("24049.02"),
            source="manual_journal",
        )
        TrialBalanceLine.objects.create(
            financial_year=cls.fy,
            account_code="4053.01",
            account_name="Physical distribution",
            closing_balance=Decimal("9866.43"),
            debit=Decimal("9866.43"),
            source="manual_journal",
        )

    def test_closing_balance_includes_brought_forward_balance(self):
        """32,703.73 b/f + 24,049.02 loaned - 9,866.43 paid = 46,886.32.

        Before the fix the Closing balance row rendered 14,183 -- the
        current-year movements with the b/f balance dropped.
        """
        buffer = _build_beneficiary_distribution_summary({"_fy": self.fy})
        self.assertIsNotNone(buffer, "summary did not render")
        text = _pdf_text(buffer)

        self.assertIn("Closing balance\n46,886", text)
        self.assertNotIn("Closing balance\n14,183", text)

    def test_closing_balance_equals_sum_of_all_trial_balance_lines(self):
        """The summary must tie to the Balance Sheet's beneficiary loan total."""
        expected = -sum(
            line.closing_balance
            for line in TrialBalanceLine.objects.filter(
                financial_year=self.fy,
                account_code__in=["4000.01", "4004.01", "4053.01"],
            )
        )
        buffer = _build_beneficiary_distribution_summary({"_fy": self.fy})
        text = _pdf_text(buffer)

        self.assertIn(f"Closing balance\n{expected:,.0f}", text)
        self.assertIn(f"Total Beneficiary Funds\n{expected:,.0f}", text)

    def test_funds_loaned_row_reports_the_actual_loan_movement(self):
        """4004.01 moved 24,049.02; the back-solved row must say so.

        Before the fix it rendered (8,655). Asserting on the label/value pair
        rather than the bare number: 24,049 also appears pre-fix as the
        opening+movement subtotal, so a bare substring match passes for the
        wrong reason.
        """
        buffer = _build_beneficiary_distribution_summary({"_fy": self.fy})
        text = _pdf_text(buffer)

        self.assertIn("Funds loaned to trust\n24,049", text)
        self.assertNotIn("Funds loaned to trust\n(8,655)", text)
