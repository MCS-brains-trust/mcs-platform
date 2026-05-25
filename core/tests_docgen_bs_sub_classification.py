"""
Regression test for Defect D.2 — docgen.py BS sub-classifier sibling
====================================================================
Sibling to commit e41a300 (Defect D in fs_template_service.py). Ensures
the inline BS sub-classifier in ``_add_detailed_balance_sheet`` at
``core/docgen.py`` reads ``AccountMapping.standard_code`` first (via a
sidecar code→standard_code dict built from the FY's TrialBalanceLines)
before falling back to the legacy account-name keyword list.

Reach: ``_add_detailed_balance_sheet`` is on the live Management Accounts
path via ``core/mgmt_accounts.py:622`` (``generate_management_accounts``
called from the "Generate Management Accounts" UI button), and on the
deprecated python-docx FS path via ``core/docgen.py:3141``.

NOTE on test surface: the sub-classifier is inline (no extractable helper
function) and the wrapper ``_add_detailed_balance_sheet`` calls
``_get_as_at_text(fy)`` which uses ``strftime('%-d %B %Y')`` — the
``%-d`` directive is Linux-only and raises ``ValueError`` on Windows.
The test patches ``_get_as_at_text`` to a constant string so the
rendering path is exercised on both platforms.

Defect D.2. See audit_hazaway_cash_classification_defect_d.md
(Phase 1 sibling identification) and phase_d2_fix_docgen_subclass.md.
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


@override_settings(STORAGES=STORAGES_OVERRIDE)
class DocgenBsSubClassificationTests(TestCase):
    """Mapped-BS-CA-001 row with no cash keyword in its name must render
    under the 'Cash and Cash Equivalents' sub-heading."""

    @classmethod
    def setUpTestData(cls):
        cls.client_obj = Client.objects.create(name="Docgen Sub-Class Test Client")
        cls.entity = Entity.objects.create(
            entity_name="Docgen Sub-Class Test Co Pty Ltd",
            entity_type="company",
            client=cls.client_obj,
        )
        cls.fy = FinancialYear.objects.create(
            entity=cls.entity,
            year_label="FY2025",
            start_date=date(2024, 7, 1),
            end_date=date(2025, 6, 30),
        )
        cls.cash_mapping = AccountMapping.objects.create(
            standard_code="BS-CA-001",
            line_item_label="Cash and cash equivalents",
            financial_statement="balance_sheet",
            statement_section="Current Assets",
        )
        # The Hazaway 2437 pattern: bank account named after the entity, mapped
        # to BS-CA-001, positive balance, no cash/bank/petty keyword in name,
        # and code_num (2437) is NOT < 2100 so the code-range fallback misses too.
        cls.bank_row = TrialBalanceLine.objects.create(
            financial_year=cls.fy,
            account_code="2437",
            account_name="HAZAWAY OPERATIONS PTY LTD",
            opening_balance=Decimal("0"),
            debit=Decimal("9816.00"),
            credit=Decimal("0"),
            closing_balance=Decimal("9816.00"),
            source="tb_import",
            mapped_line_item=cls.cash_mapping,
        )

    def _render_and_collect_rows(self):
        """Render the detailed balance sheet and return a flat list of
        ``row.cells[0].text`` strings for every row in every table, in
        document order. The merged sub-heading row's text lands in
        ``cells[0]`` so this is enough to assert ordering.

        Patches ``_get_as_at_text`` to avoid the Linux-only ``%-d``
        strftime directive on Windows test runs.
        """
        sections = _get_tb_sections(self.fy)
        doc = Document()
        with patch.object(docgen, "_get_as_at_text", return_value="as at 30 June 2025"):
            _add_detailed_balance_sheet(doc, self.entity, self.fy, sections)
        rows_text = []
        for table in doc.tables:
            for row in table.rows:
                rows_text.append(row.cells[0].text.strip())
        return rows_text

    def test_mapped_bsca001_no_keyword_lands_under_cash_and_cash_equivalents(self):
        rows = self._render_and_collect_rows()

        # Locate the "Cash and Cash Equivalents" sub-heading row.
        cash_heading_indices = [
            i for i, text in enumerate(rows)
            if text == "Cash and Cash Equivalents"
        ]
        self.assertEqual(
            len(cash_heading_indices), 1,
            f"Expected exactly one 'Cash and Cash Equivalents' sub-heading row; "
            f"got rows={rows!r}",
        )
        heading_idx = cash_heading_indices[0]

        # The HAZAWAY OPERATIONS row must appear after the cash heading and
        # before the next sub-heading / section heading / total.
        # Sub-headings emitted in this section: "Cash and Cash Equivalents",
        # "Receivables", "Inventories". Total row label: "Total Current Assets".
        other_headings = {
            "Receivables", "Inventories", "Total Current Assets",
            "Current Assets",
        }
        next_break_idx = next(
            (i for i in range(heading_idx + 1, len(rows)) if rows[i] in other_headings),
            len(rows),
        )
        slice_under_cash = rows[heading_idx + 1: next_break_idx]
        self.assertTrue(
            any("HAZAWAY OPERATIONS" in t for t in slice_under_cash),
            f"HAZAWAY OPERATIONS row must appear under 'Cash and Cash Equivalents' "
            f"heading; rows between heading and next break: {slice_under_cash!r}",
        )

        # Sanity: legacy 'Cash Assets' string must not appear anywhere.
        self.assertNotIn(
            "Cash Assets", rows,
            "Legacy 'Cash Assets' label must be renamed to 'Cash and Cash "
            "Equivalents' everywhere in _add_detailed_balance_sheet.",
        )
