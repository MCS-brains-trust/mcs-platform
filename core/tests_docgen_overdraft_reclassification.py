"""
Regression test for Defect D.3 — docgen year-by-year overdraft split
=====================================================================
Verifies the BS-CA-001 sign-aware reclassification in
``core/docgen.py:_add_detailed_balance_sheet`` (sibling to
``core/fs_template_service.py`` Defect D / commit e41a300).

Per row mapped to ``AccountMapping.standard_code == "BS-CA-001"``:
positive balances stay in Cash and Cash Equivalents (in that year's
column only); negative balances move sign-flipped to a new "Bank
Overdrafts" sub-section in current_liabilities (in that year's column
only). The same account can appear in BOTH sections when CY and PY
signs differ. Empty cells render as "-" (ASCII hyphen) via
``core/table_helpers.py:_fmt``.

Scope per spec: only BS-CA-001-mapped rows are split. Keyword-fallback
cash rows (matched by name/code-range without a BS-CA-001 mapping)
retain today's behaviour — Test 5 confirms this.

Defect D.3. See phase_d3_docgen_signflip.md.

NOTE on test surface: same pattern as Defect D.2's test —
``_add_detailed_balance_sheet`` calls ``_get_as_at_text(fy)`` which
uses ``strftime('%-d %B %Y')`` (Linux-only). The test patches that
helper to a constant string so the rendering path is exercised on
both Windows and Linux.
"""
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from docx import Document

from core import docgen
from core.docgen import _add_detailed_balance_sheet, _get_tb_sections
from core.models import (
    AccountMapping,
    Client,
    Entity,
    FinancialYear,
    TrialBalanceLine,
)


STORAGES_OVERRIDE = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


# Sets used to walk the rendered docx and detect section / sub-heading rows.
# Mirrors the structure docgen emits for company BS.
_SECTION_NAMES = {
    "Current Assets", "Non-Current Assets", "Current Liabilities",
    "Non-Current Liabilities", "Equity",
}
_SUB_HEADING_NAMES = {
    "Cash and Cash Equivalents", "Receivables", "Inventories",
    "Bank Overdrafts",
    "Payables", "Current Tax Liabilities", "Provisions",
    "Financial Liabilities", "Secured:", "Unsecured:",
}


@override_settings(STORAGES=STORAGES_OVERRIDE)
class OverdraftReclassificationTests(TestCase):
    """Six spec cases + one gating-coverage case (test 7)."""

    @classmethod
    def setUpTestData(cls):
        cls.client_obj = Client.objects.create(name="Overdraft Test Client")
        cls.entity = Entity.objects.create(
            entity_name="Overdraft Test Co Pty Ltd",
            entity_type="company",
            client=cls.client_obj,
        )
        # Prior FY with at least one TBL so _has_prior_year returns True
        # and the rendered BS table includes a prior-year column. The
        # tests then verify both columns of each row.
        cls.prior_fy = FinancialYear.objects.create(
            entity=cls.entity,
            year_label="FY2024",
            start_date=date(2023, 7, 1),
            end_date=date(2024, 6, 30),
        )
        TrialBalanceLine.objects.create(
            financial_year=cls.prior_fy,
            account_code="9999",
            account_name="prior placeholder",
            opening_balance=Decimal("0"),
            debit=Decimal("0"),
            credit=Decimal("0"),
            closing_balance=Decimal("0"),
            source="tb_import",
        )
        cls.fy = FinancialYear.objects.create(
            entity=cls.entity,
            year_label="FY2025",
            start_date=date(2024, 7, 1),
            end_date=date(2025, 6, 30),
            prior_year=cls.prior_fy,
        )
        cls.cash_mapping = AccountMapping.objects.create(
            standard_code="BS-CA-001",
            line_item_label="Cash and cash equivalents",
            financial_statement="balance_sheet",
            statement_section="Current Assets",
        )
        cls.other_ca_mapping = AccountMapping.objects.create(
            standard_code="BS-CA-005",
            line_item_label="Other current assets",
            financial_statement="balance_sheet",
            statement_section="Current Assets",
        )

    def _make_bank_row(self, code, name, current, prior, mapping=None):
        """Create a TBL with closing_balance=current and prior cols set so
        _get_tb_sections produces (current_amount, prior_amount) = (current, prior).

        prior_amount in docgen = line.prior_debit - line.prior_credit.
        """
        c = Decimal(str(current))
        p = Decimal(str(prior))
        return TrialBalanceLine.objects.create(
            financial_year=self.fy,
            account_code=code,
            account_name=name,
            opening_balance=Decimal("0"),
            debit=c if c > 0 else Decimal("0"),
            credit=abs(c) if c < 0 else Decimal("0"),
            closing_balance=c,
            prior_debit=p if p > 0 else Decimal("0"),
            prior_credit=abs(p) if p < 0 else Decimal("0"),
            source="tb_import",
            mapped_line_item=mapping,
        )

    def _render_rows(self):
        """Render the detailed BS and return a list of cell-text lists
        (one inner list per row, across all tables in the doc).
        """
        sections = _get_tb_sections(self.fy)
        doc = Document()
        with patch.object(docgen, "_get_as_at_text", return_value="as at 30 June 2025"):
            _add_detailed_balance_sheet(doc, self.entity, self.fy, sections)
        rows = []
        for table in doc.tables:
            for row in table.rows:
                rows.append([c.text.strip() for c in row.cells])
        return rows

    def _find_account(self, rows, name_substr):
        """Return a list of ``(sub_heading, cells)`` tuples for every row
        whose first cell contains ``name_substr``. Sub-heading is the most
        recent sub-heading row seen (resets to None on section heading).
        """
        matches = []
        current_sub = None
        for row in rows:
            first = row[0] if row else ""
            if first in _SECTION_NAMES:
                current_sub = None
                continue
            if first in _SUB_HEADING_NAMES:
                current_sub = first
                continue
            if not first:
                continue
            if name_substr in first:
                matches.append((current_sub, row))
        return matches

    # ------------------------------------------------------------------
    # Test 1 — both years positive: stays in Cash only
    # ------------------------------------------------------------------
    def test_both_positive_in_cash_only(self):
        self._make_bank_row("2440", "BankA", "100000.00", "80000.00",
                            mapping=self.cash_mapping)
        # Sibling positive row so cash_items has 2 entries and a subtotal
        # row is emitted (verifies cash rendering path stays clean).
        self._make_bank_row("2441", "BankA-Sub", "5000.00", "3000.00",
                            mapping=self.cash_mapping)

        rows = self._render_rows()
        cash_matches = self._find_account(rows, "BankA")
        # Both BankA and BankA-Sub should be under Cash and Cash Equivalents.
        cash_hits = [m for m in cash_matches if m[0] == "Cash and Cash Equivalents"]
        self.assertEqual(len(cash_hits), 2,
                         f"BankA + BankA-Sub must both render under Cash; got {cash_matches!r}")
        # Find the BankA exact row (not BankA-Sub) and verify amounts
        bank_a_exact = [m for m in cash_hits if m[1][0] == "BankA"]
        self.assertEqual(len(bank_a_exact), 1)
        _sub, cells = bank_a_exact[0]
        self.assertEqual(cells[2], "100,000",
                         f"BankA current must be 100,000; got {cells[2]!r}")
        self.assertEqual(cells[3], "80,000",
                         f"BankA prior must be 80,000; got {cells[3]!r}")
        # Must NOT appear in Bank Overdrafts.
        overdraft_hits = [m for m in cash_matches if m[0] == "Bank Overdrafts"]
        self.assertEqual(overdraft_hits, [],
                         f"BankA must NOT appear in Bank Overdrafts; got {overdraft_hits!r}")

    # ------------------------------------------------------------------
    # Test 2 — both years negative: moves to Bank Overdrafts only
    # ------------------------------------------------------------------
    def test_both_negative_in_bank_overdrafts_only(self):
        self._make_bank_row("2436", "BankB", "-50000.00", "-60000.00",
                            mapping=self.cash_mapping)

        rows = self._render_rows()
        matches = self._find_account(rows, "BankB")
        # Must NOT appear in Cash.
        cash_hits = [m for m in matches if m[0] == "Cash and Cash Equivalents"]
        self.assertEqual(cash_hits, [],
                         f"BankB must NOT appear in Cash; got {cash_hits!r}")
        # MUST appear under Bank Overdrafts, sign-flipped to positive.
        overdraft_hits = [m for m in matches if m[0] == "Bank Overdrafts"]
        self.assertEqual(len(overdraft_hits), 1,
                         f"BankB must appear once under Bank Overdrafts; got {matches!r}")
        cells = overdraft_hits[0][1]
        self.assertEqual(cells[2], "50,000",
                         f"BankB current must be sign-flipped to 50,000; got {cells[2]!r}")
        self.assertEqual(cells[3], "60,000",
                         f"BankB prior must be sign-flipped to 60,000; got {cells[3]!r}")

    # ------------------------------------------------------------------
    # Test 3 — mixed (CY positive, PY negative): row appears in BOTH sections
    # ------------------------------------------------------------------
    def test_mixed_cy_pos_py_neg_appears_in_both(self):
        self._make_bank_row("2440", "BankC", "9816.00", "-67360.00",
                            mapping=self.cash_mapping)

        rows = self._render_rows()
        matches = self._find_account(rows, "BankC")
        cash_hits = [m for m in matches if m[0] == "Cash and Cash Equivalents"]
        overdraft_hits = [m for m in matches if m[0] == "Bank Overdrafts"]
        self.assertEqual(len(cash_hits), 1,
                         f"BankC must appear once under Cash; got {matches!r}")
        self.assertEqual(len(overdraft_hits), 1,
                         f"BankC must appear once under Bank Overdrafts; got {matches!r}")
        # Cash row: current=9,816, prior=-
        cash_cells = cash_hits[0][1]
        self.assertEqual(cash_cells[2], "9,816",
                         f"Cash row current must be 9,816; got {cash_cells[2]!r}")
        self.assertEqual(cash_cells[3], "-",
                         f"Cash row prior must be '-' (hyphen) since PY was negative; "
                         f"got {cash_cells[3]!r}")
        # Overdraft row: current=-, prior=67,360 (sign-flipped)
        ovr_cells = overdraft_hits[0][1]
        self.assertEqual(ovr_cells[2], "-",
                         f"Overdraft row current must be '-' since CY was positive; "
                         f"got {ovr_cells[2]!r}")
        self.assertEqual(ovr_cells[3], "67,360",
                         f"Overdraft row prior must be sign-flipped 67,360; "
                         f"got {ovr_cells[3]!r}")

    # ------------------------------------------------------------------
    # Test 4 — mixed (CY negative, PY positive): row appears in BOTH sections
    # ------------------------------------------------------------------
    def test_mixed_cy_neg_py_pos_appears_in_both(self):
        self._make_bank_row("2436", "BankD", "-56061.00", "3242.00",
                            mapping=self.cash_mapping)

        rows = self._render_rows()
        matches = self._find_account(rows, "BankD")
        cash_hits = [m for m in matches if m[0] == "Cash and Cash Equivalents"]
        overdraft_hits = [m for m in matches if m[0] == "Bank Overdrafts"]
        self.assertEqual(len(cash_hits), 1,
                         f"BankD must appear once under Cash; got {matches!r}")
        self.assertEqual(len(overdraft_hits), 1,
                         f"BankD must appear once under Bank Overdrafts; got {matches!r}")
        cash_cells = cash_hits[0][1]
        self.assertEqual(cash_cells[2], "-",
                         f"Cash row current must be '-' since CY was negative; "
                         f"got {cash_cells[2]!r}")
        self.assertEqual(cash_cells[3], "3,242",
                         f"Cash row prior must be 3,242; got {cash_cells[3]!r}")
        ovr_cells = overdraft_hits[0][1]
        self.assertEqual(ovr_cells[2], "56,061",
                         f"Overdraft row current must be sign-flipped 56,061; "
                         f"got {ovr_cells[2]!r}")
        self.assertEqual(ovr_cells[3], "-",
                         f"Overdraft row prior must be '-' since PY was positive; "
                         f"got {ovr_cells[3]!r}")

    # ------------------------------------------------------------------
    # Test 5 — non-BS-CA-001 mapping: NOT split. Spec scope constraint.
    # ------------------------------------------------------------------
    def test_non_cash_mapping_not_split(self):
        # Mapped to BS-CA-005 (Other current assets), name lacks any cash/bank
        # keyword so the keyword fallback also doesn't promote it. It must land
        # in other_ca_items and stay there even with a negative balance.
        self._make_bank_row("2495", "Prepayment Asset", "-50000.00", "-60000.00",
                            mapping=self.other_ca_mapping)

        rows = self._render_rows()
        matches = self._find_account(rows, "Prepayment Asset")
        # MUST NOT appear under Bank Overdrafts.
        overdraft_hits = [m for m in matches if m[0] == "Bank Overdrafts"]
        self.assertEqual(overdraft_hits, [],
                         f"Non-BS-CA-001 row must NOT be split into Bank Overdrafts; "
                         f"got {overdraft_hits!r}")
        # Sanity: it should appear SOMEWHERE in the doc.
        self.assertTrue(matches, "Prepayment Asset row not rendered at all — setup broken")
        # And the Bank Overdrafts heading must not exist in the whole doc.
        self.assertFalse(
            any(row[0] == "Bank Overdrafts" for row in rows),
            "Bank Overdrafts sub-heading must NOT appear when no BS-CA-001 negatives exist",
        )

    # ------------------------------------------------------------------
    # Test 6 — only positive cash accounts: no Bank Overdrafts heading
    # ------------------------------------------------------------------
    def test_positive_only_suppresses_overdrafts_heading(self):
        self._make_bank_row("2437", "PositiveBank", "5000.00", "3000.00",
                            mapping=self.cash_mapping)
        # Add a second account so cash_items rendering has more than one
        # entry (matches the typical FY layout).
        self._make_bank_row("2438", "Savings", "100.00", "100.00",
                            mapping=self.cash_mapping)

        rows = self._render_rows()
        self.assertFalse(
            any(row[0] == "Bank Overdrafts" for row in rows),
            "Bank Overdrafts heading must NOT appear when overdraft_items is empty",
        )
        # And both positive accounts must appear under Cash.
        pos_matches = self._find_account(rows, "PositiveBank")
        sav_matches = self._find_account(rows, "Savings")
        self.assertTrue(
            any(m[0] == "Cash and Cash Equivalents" for m in pos_matches),
            f"PositiveBank must render under Cash; got {pos_matches!r}",
        )
        self.assertTrue(
            any(m[0] == "Cash and Cash Equivalents" for m in sav_matches),
            f"Savings must render under Cash; got {sav_matches!r}",
        )

    # ------------------------------------------------------------------
    # Test 7 — overdraft-only entity (no other current_liabilities):
    # the widened gating must still render the Current Liabilities section.
    # ------------------------------------------------------------------
    def test_overdraft_only_entity_renders_current_liabilities_section(self):
        # A BS-CA-001-mapped negative balance in current_assets, AND a
        # trade debtor to give current_assets some non-overdraft content
        # (so the rest of the BS renders normally). Crucially, NO TBLs
        # in current_liabilities code range (3000-3499).
        self._make_bank_row("2436", "LoneOverdraft", "-50000.00", "0",
                            mapping=self.cash_mapping)
        self._make_bank_row("2199", "Trade debtors", "1000.00", "1000.00",
                            mapping=None)

        rows = self._render_rows()
        # The widened gate (sections["current_liabilities"] or overdraft_items)
        # must allow the section to render even though sections["current_liabilities"]
        # is empty.
        self.assertIn(
            "Current Liabilities", [row[0] for row in rows],
            f"Current Liabilities section must render even with no payables/tax/"
            f"provisions, because overdraft_items is non-empty. Rows={[r[0] for r in rows]!r}",
        )
        self.assertIn(
            "Bank Overdrafts", [row[0] for row in rows],
            f"Bank Overdrafts sub-heading must appear. "
            f"Rows={[r[0] for r in rows]!r}",
        )
        matches = self._find_account(rows, "LoneOverdraft")
        overdraft_hits = [m for m in matches if m[0] == "Bank Overdrafts"]
        self.assertEqual(len(overdraft_hits), 1,
                         f"LoneOverdraft must appear once under Bank Overdrafts; "
                         f"got {matches!r}")
        cells = overdraft_hits[0][1]
        self.assertEqual(cells[2], "50,000",
                         f"Sign-flipped current must be 50,000; got {cells[2]!r}")
        self.assertEqual(cells[3], "-",
                         f"PY was 0 → must render as '-'; got {cells[3]!r}")
