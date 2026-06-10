"""
Regression test — Detailed BS: fully-depreciated NCA class no longer suppressed
================================================================================
Covers the fix at core/fs_template_service.py:1372 where `has_noncurrent_assets`
was computed from the *net* of the non-current assets section via _sum_section().
For a fully-depreciated fixed-asset class (cost DR + accum dep CR = net $0) the
net was zero, making the flag False and suppressing the entire Non-Current Assets
section on the detailed balance sheet face.

The fix: `has_noncurrent_assets` is now True when *any individual line* in
sections["noncurrent_assets"] has a non-zero cy_amount or py_amount balance.

These tests call _get_tb_sections and _sum_section directly (not the full
build_company_context orchestrator, which has a Linux-only strftime call unrelated
to this fix).
"""
from datetime import date
from decimal import Decimal

from django.test import TestCase, override_settings

from core.fs_template_service import _get_tb_sections, _sum_section
from core.models import Client, Entity, FinancialYear, TrialBalanceLine


STORAGES_OVERRIDE = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


def _compute_has_noncurrent_assets(sections):
    """Mirror of the fixed fs_template_service.py:1372 logic."""
    return any(
        (item.get("cy_amount") or 0) != 0 or (item.get("py_amount") or 0) != 0
        for item in sections["noncurrent_assets"]
    )


@override_settings(STORAGES=STORAGES_OVERRIDE)
class FullyDepreciatedNcaSuppressionTests(TestCase):
    """has_noncurrent_assets must be True for a fully-depreciated asset class."""

    @classmethod
    def setUpTestData(cls):
        cls.client_obj = Client.objects.create(name="NCA Suppression Test Client")
        cls.entity = Entity.objects.create(
            entity_name="Huxley Test Co Pty Ltd",
            entity_type="company",
            client=cls.client_obj,
        )
        cls.fy = FinancialYear.objects.create(
            entity=cls.entity,
            year_label="FY2025",
            start_date=date(2024, 7, 1),
            end_date=date(2025, 6, 30),
        )

    def _make_row(self, code, name, cy, py="0.00", source="tb_import"):
        kwargs = dict(
            financial_year=self.fy,
            account_code=code,
            account_name=name,
            opening_balance=Decimal("0"),
            debit=Decimal("0"),
            credit=Decimal("0"),
            closing_balance=Decimal(cy),
            prior_debit=Decimal(py) if Decimal(py) >= 0 else Decimal("0"),
            prior_credit=Decimal("0") if Decimal(py) >= 0 else abs(Decimal(py)),
            source=source,
        )
        return TrialBalanceLine.objects.create(**kwargs)

    # ------------------------------------------------------------------
    # Test 1 — Primary: fully-depreciated class must not suppress section
    # ------------------------------------------------------------------
    def test_fully_depreciated_class_has_noncurrent_assets_true(self):
        """Cost $42,713.20 (DR) + accum dep $42,713.20 (CR) → net = $0.
        has_noncurrent_assets must be True; section must contain both lines;
        total_noncurrent_assets_cy must be $0 (subtotal correctly nil)."""
        # Account codes 2890-2899 fall in the noncurrent_assets bucket (2500–2999)
        self._make_row("2890", "Motor vehicles (at cost)", "42713.20")
        self._make_row("2895", "Less: Accumulated depreciation - motor vehicles", "-42713.20")

        sections = _get_tb_sections(self.fy)

        # Assertion 1: has_noncurrent_assets is True
        has_nca = _compute_has_noncurrent_assets(sections)
        self.assertTrue(
            has_nca,
            "has_noncurrent_assets must be True when individual NCA lines are "
            "non-zero even if their net is zero",
        )

        # Assertion 2: both lines present in sections["noncurrent_assets"]
        nca_codes = [item["account_code"] for item in sections["noncurrent_assets"]]
        self.assertIn("2890", nca_codes, "Cost line (2890) must appear in noncurrent_assets")
        self.assertIn("2895", nca_codes, "Accumulated dep line (2895) must appear in noncurrent_assets")

        # Assertion 3: subtotal correctly nets to nil
        total_nca_cy = _sum_section(sections["noncurrent_assets"])
        self.assertEqual(
            total_nca_cy,
            Decimal("0"),
            f"total_noncurrent_assets_cy must net to 0 for fully-depreciated class; got {total_nca_cy}",
        )

    # ------------------------------------------------------------------
    # Test 2 — Non-regression: entity with no NCA still suppresses section
    # ------------------------------------------------------------------
    def test_no_nca_section_suppressed(self):
        """Entity with no non-current asset accounts: has_noncurrent_assets must be False."""
        # Only add a current-asset row (code 2100 → current_assets bucket)
        self._make_row("2100", "Trade debtors", "15000.00")

        sections = _get_tb_sections(self.fy)

        has_nca = _compute_has_noncurrent_assets(sections)
        self.assertFalse(
            has_nca,
            "has_noncurrent_assets must be False when sections['noncurrent_assets'] is empty",
        )

    # ------------------------------------------------------------------
    # Test 3 — Non-regression: normal (non-nil NBV) NCA still renders
    # ------------------------------------------------------------------
    def test_normal_nca_has_noncurrent_assets_true(self):
        """Asset with NBV > 0: has_noncurrent_assets must be True and net > 0."""
        self._make_row("2890", "Motor vehicles (at cost)", "80000.00")
        self._make_row("2895", "Less: Accumulated depreciation - motor vehicles", "-30000.00")

        sections = _get_tb_sections(self.fy)

        has_nca = _compute_has_noncurrent_assets(sections)
        self.assertTrue(has_nca, "has_noncurrent_assets must be True for normal NCA")

        total_nca_cy = _sum_section(sections["noncurrent_assets"])
        self.assertEqual(
            total_nca_cy,
            Decimal("50000.00"),
            f"Subtotal must be 80,000 - 30,000 = 50,000; got {total_nca_cy}",
        )
