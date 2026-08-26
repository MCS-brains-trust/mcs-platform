"""The section classifier must honour the chart's declared section.

``_get_tb_sections`` (core/fs_template_service.py) and ``_classify_tb_lines``
(core/document_context_builder.py) both classified every trial-balance line by
NUMERIC CODE RANGE alone, ignoring ``EntityChartOfAccount.section`` entirely.
Every code 4000-4999 landed in "equity" regardless of what the chart said, so
moving a beneficiary loan from 4004.NN to 4110.NN -- to reclassify it from
equity to a current liability -- achieved nothing in the rendered statements:
the chart said ``section="liabilities"`` but the builder still filed it under
equity (verified live on Dr Services FY2026: 4110.01, cy=-24049.02).

This module proves the fix: the chart's ``section`` is now consulted first
(full account_code, then the parent before the dot, so per-beneficiary
children inherit); only when the chart has no usable answer -- no row, no
section, or a section this builder does not have an unambiguous target for --
does classification fall back to the numeric range exactly as before.
"""
from datetime import date
from decimal import Decimal

from django.test import TestCase

from core.document_context_builder import DocumentContextBuilder
from core.fs_template_service import _get_tb_sections
from core.models import EntityChartOfAccount, FinancialYear, TrialBalanceLine
from core.tests_beneficiary_accounts import BeneficiaryAccountTestBase


def _tb_line(fy, code, name, cy):
    """A minimal TrialBalanceLine: cy positive = debit, negative = credit."""
    return TrialBalanceLine.objects.create(
        financial_year=fy,
        account_code=code,
        account_name=name,
        closing_balance=cy,
        debit=cy if cy > 0 else Decimal("0"),
        credit=-cy if cy < 0 else Decimal("0"),
        source="tb_import",
        is_adjustment=False,
    )


class SectionFromChartTests(BeneficiaryAccountTestBase):
    """``self.trust`` (entity_type="trust") comes from BeneficiaryAccountTestBase,
    which also seeds a parent EntityChartOfAccount row for code "4110" with
    ``section="liabilities"`` (core/beneficiary_account_service.py:44) -- the
    real beneficiary-loan parent code from the brief.
    """

    def setUp(self):
        self.fy = FinancialYear.objects.create(
            entity=self.trust,
            year_label="FY2026",
            start_date=date(2025, 7, 1),
            end_date=date(2026, 6, 30),
            status=FinancialYear.Status.DRAFT,
        )

    # ── Step 1, test 1: chart-declared section wins over the numeric range ──

    def test_4110_01_with_chart_section_liabilities_is_a_current_liability(self):
        """The defect this task fixes. 4110.01 sits in the 4000-4999 numeric
        range, which the old classifier always sent to "equity" -- but the
        chart declares it "liabilities", so it must land in
        current_liabilities instead."""
        EntityChartOfAccount.objects.update_or_create(
            entity=self.trust, account_code="4110.01",
            defaults={
                "account_name": "Funds loaned to trust — Beneficiary One",
                "section": "liabilities",
                "is_active": True,
            },
        )
        _tb_line(self.fy, "4110.01", "Funds loaned to trust — Beneficiary One",
                 Decimal("-24049.02"))

        sections = _get_tb_sections(self.fy)

        codes_in_equity = {i["account_code"] for i in sections["equity"]}
        codes_in_current_liab = {i["account_code"] for i in sections["current_liabilities"]}
        self.assertNotIn("4110.01", codes_in_equity)
        self.assertIn("4110.01", codes_in_current_liab)

    # ── Step 1, test 2: fallback is unchanged when there is no chart row ────

    def test_code_with_no_chart_row_still_classifies_by_numeric_range(self):
        """Regression guard: this must pass BOTH before and after the fix.
        A code with no EntityChartOfAccount row at all (4200, not seeded by
        the base fixture) has no chart answer, so it must classify exactly
        as the old numeric-range logic did: 4200 < 5000 -> equity."""
        _tb_line(self.fy, "4200", "Trust corpus", Decimal("-90000"))

        sections = _get_tb_sections(self.fy)

        codes_in_equity = {i["account_code"] for i in sections["equity"]}
        self.assertIn("4200", codes_in_equity)

    # ── Design constraint: child accounts inherit the parent's section ──────

    def test_child_account_with_no_own_row_inherits_parent_chart_section(self):
        """4110.02 has no EntityChartOfAccount row of its own, but the base
        fixture seeds "4110" (the parent) with section="liabilities". The
        child must inherit it rather than falling back to the numeric range.

        Built with bulk_create, not the ``_tb_line`` helper: a plain
        ``.create()`` fires ``core.signals.ensure_chart_account_for_tb_line``
        (core/coa_sync.py), which would auto-provision a chart row for
        4110.02 itself before the assertion runs, masking exactly the "no
        row of its own" scenario this test exists to cover. bulk_create is
        the same mechanism core/access_ledger_import.py uses to reach the
        trial balance without that side effect.
        """
        TrialBalanceLine.objects.bulk_create([TrialBalanceLine(
            financial_year=self.fy, account_code="4110.02",
            account_name="Funds loaned to trust — Beneficiary Two",
            closing_balance=Decimal("-5000"), debit=Decimal("0"),
            credit=Decimal("5000"), source="tb_import", is_adjustment=False,
        )])

        sections = _get_tb_sections(self.fy)

        codes_in_current_liab = {i["account_code"] for i in sections["current_liabilities"]}
        codes_in_equity = {i["account_code"] for i in sections["equity"]}
        self.assertIn("4110.02", codes_in_current_liab)
        self.assertNotIn("4110.02", codes_in_equity)

    # ── Design constraint: an unrecognised chart section also falls back ────

    def test_unrecognised_chart_section_falls_back_to_numeric_range(self):
        """"suspense" has no unambiguous single builder-section target, so it
        must not be treated as a recognised override -- 4300 falls back to
        the numeric range (< 5000 -> equity), same as if there were no chart
        row at all."""
        EntityChartOfAccount.objects.update_or_create(
            entity=self.trust, account_code="4300",
            defaults={
                "account_name": "Unclassified suspense item",
                "section": "suspense",
                "is_active": True,
            },
        )
        _tb_line(self.fy, "4300", "Unclassified suspense item", Decimal("-1000"))

        sections = _get_tb_sections(self.fy)

        codes_in_equity = {i["account_code"] for i in sections["equity"]}
        self.assertIn("4300", codes_in_equity)

    # ── Step 5: document_context_builder._classify_tb_lines must agree ──────

    def test_document_builder_classifies_4110_01_as_current_liability_too(self):
        """Same fixture, same assertion, against the OTHER copy of this logic
        (core/document_context_builder.py) so the legal documents and the
        financial statements do not contradict each other."""
        EntityChartOfAccount.objects.update_or_create(
            entity=self.trust, account_code="4110.01",
            defaults={
                "account_name": "Funds loaned to trust — Beneficiary One",
                "section": "liabilities",
                "is_active": True,
            },
        )
        line = _tb_line(self.fy, "4110.01", "Funds loaned to trust — Beneficiary One",
                         Decimal("-24049.02"))

        builder = DocumentContextBuilder(self.trust, financial_year=self.fy)
        sections = builder._classify_tb_lines([line])

        codes_in_equity = {i["account_code"] for i in sections["equity"]}
        codes_in_current_liab = {i["account_code"] for i in sections["current_liabilities"]}
        self.assertNotIn("4110.01", codes_in_equity)
        self.assertIn("4110.01", codes_in_current_liab)

    def test_document_builder_fallback_unchanged_with_no_chart_row(self):
        """Regression guard for the document builder, mirroring the
        fs_template_service fallback test above."""
        line = _tb_line(self.fy, "4200", "Trust corpus", Decimal("-90000"))

        builder = DocumentContextBuilder(self.trust, financial_year=self.fy)
        sections = builder._classify_tb_lines([line])

        codes_in_equity = {i["account_code"] for i in sections["equity"]}
        self.assertIn("4200", codes_in_equity)
