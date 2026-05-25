"""
Regression test for core/fs_template_service.py:_get_tb_sections
================================================================
Covers Defect A from audit_hazaway_fy25_fs_regression.md: under Model A
row-shape (commit cb00bf1, 2026-05-20), a Balance Sheet account has TWO
TrialBalanceLine rows for the same account_code — one source='rollover'
carrying the opening in closing_balance, and one source='tb_import'
carrying the period movement in closing_balance. The reader must sum
closing_balance across both rows to produce the correct CY closing.

Before the fix, _get_tb_sections set cy=0 for rollover rows, so CY was
read as movement only — giving CY = (CY_closing - PY_closing) for every
BS account. P&L was unaffected because P&L openings are structurally
zero, so movement equals closing.
"""
from datetime import date
from decimal import Decimal

from django.test import TestCase, override_settings

from core.fs_template_service import _get_tb_sections
from core.models import Client, Entity, FinancialYear, TrialBalanceLine


STORAGES_OVERRIDE = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@override_settings(STORAGES=STORAGES_OVERRIDE)
class GetTbSectionsModelAAggregationTests(TestCase):
    """Aggregation of rollover + tb_import rows under Model A row-shape."""

    @classmethod
    def setUpTestData(cls):
        cls.client_obj = Client.objects.create(name="FS Aggregation Test Client")
        cls.entity = Entity.objects.create(
            entity_name="Aggregation Test Co Pty Ltd",
            entity_type="company",
            client=cls.client_obj,
        )
        cls.fy = FinancialYear.objects.create(
            entity=cls.entity,
            year_label="FY2025",
            start_date=date(2024, 7, 1),
            end_date=date(2025, 6, 30),
        )

    def _find_entry(self, sections, account_code):
        """Return the aggregated entry dict for account_code, or None."""
        for items in sections.values():
            for item in items:
                if item.get("account_code") == account_code:
                    return item
        return None

    def test_bs_rollover_plus_tb_import_sum_to_full_closing(self):
        """BS account: rollover.closing (opening) + tb_import.closing (movement)
        must aggregate to CY = full year-end closing balance, and PY = rollover's
        prior_debit - prior_credit (the PY closing)."""
        # Rollover row — carries opening (= PY closing) in closing_balance,
        # PY value in prior_debit/credit
        TrialBalanceLine.objects.create(
            financial_year=self.fy,
            account_code="2101",
            account_name="Trade debtors",
            opening_balance=Decimal("100000.00"),
            debit=Decimal("0"),
            credit=Decimal("0"),
            closing_balance=Decimal("100000.00"),
            prior_debit=Decimal("100000.00"),
            prior_credit=Decimal("0"),
            source="rollover",
        )
        # tb_import row — carries period movement only (opening = 0)
        TrialBalanceLine.objects.create(
            financial_year=self.fy,
            account_code="2101",
            account_name="Trade debtors",
            opening_balance=Decimal("0"),
            debit=Decimal("0"),
            credit=Decimal("25000.00"),
            closing_balance=Decimal("-25000.00"),
            prior_debit=Decimal("0"),
            prior_credit=Decimal("0"),
            source="tb_import",
        )

        sections = _get_tb_sections(self.fy)
        entry = self._find_entry(sections, "2101")
        self.assertIsNotNone(entry, "Account 2101 should appear in aggregated sections")
        self.assertEqual(
            entry["cy_amount"],
            Decimal("75000.00"),
            "CY must be opening + movement = 100,000 + (-25,000) = 75,000",
        )
        self.assertEqual(
            entry["py_amount"],
            Decimal("100000.00"),
            "PY must be the rollover row's prior_debit - prior_credit",
        )

    def test_pl_tb_import_only_returns_movement_as_cy(self):
        """P&L account: only a tb_import row (no rollover for structurally-zero
        P&L opening). CY = closing_balance directly. Parity check — the fix
        must not regress P&L behaviour."""
        TrialBalanceLine.objects.create(
            financial_year=self.fy,
            account_code="0630",
            account_name="Asbestos Removal",
            opening_balance=Decimal("0"),
            debit=Decimal("0"),
            credit=Decimal("50000.00"),
            closing_balance=Decimal("-50000.00"),
            prior_debit=Decimal("0"),
            prior_credit=Decimal("0"),
            source="tb_import",
        )

        sections = _get_tb_sections(self.fy)
        entry = self._find_entry(sections, "0630")
        self.assertIsNotNone(entry, "Account 0630 should appear in aggregated sections")
        self.assertEqual(
            entry["cy_amount"],
            Decimal("-50000.00"),
            "P&L CY must equal tb_import.closing_balance unchanged",
        )
