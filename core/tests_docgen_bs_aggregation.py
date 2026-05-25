"""
Regression test for core/docgen.py:_get_tb_sections
===================================================
Parallel to core/tests_fs_template_service_bs_aggregation.py (Phase 2a,
commit 01a5e91). docgen._get_tb_sections is a structural sibling of the
fs_template_service version and feeds Management Accounts via
core/mgmt_accounts.py:build_manual_tb_sections.

Under Model A row-shape (commit cb00bf1, 2026-05-20), a Balance Sheet
account has TWO TrialBalanceLine rows for the same account_code — one
source='rollover' carrying the opening in closing_balance, and one
source='tb_import' carrying the period movement in closing_balance. The
reader must sum closing_balance across both rows to produce the correct
CY closing.

Before this fix, docgen used `debit − credit` as CY for every row. Under
Model A: rollover.debit=credit=0 contributes 0; tb_import.debit−credit
equals movement. Sum = movement only — Defect A symptom on the mgmt
accounts pipeline. P&L unaffected because P&L openings are structurally
zero, so movement equals closing.

NOTE: docgen._get_tb_sections returns 4-tuples (account_code, account_name,
current_amount, prior_amount), not dicts. Assertions read tuple positions.
"""
from datetime import date
from decimal import Decimal

from django.test import TestCase, override_settings

from core.docgen import _get_tb_sections
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
        cls.client_obj = Client.objects.create(name="Docgen Aggregation Test Client")
        cls.entity = Entity.objects.create(
            entity_name="Docgen Aggregation Test Co Pty Ltd",
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
        """Return the aggregated 4-tuple (code, name, current, prior) for
        account_code, or None. docgen returns tuples not dicts."""
        for items in sections.values():
            for item in items:
                if item[0] == account_code:
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
        # entry = (account_code, account_name, current_amount, prior_amount)
        self.assertEqual(
            entry[2],
            Decimal("75000.00"),
            "CY must be opening + movement = 100,000 + (-25,000) = 75,000",
        )
        self.assertEqual(
            entry[3],
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
            entry[2],
            Decimal("-50000.00"),
            "P&L CY must equal tb_import.closing_balance unchanged",
        )
