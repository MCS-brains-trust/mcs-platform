"""Every trial-balance line must have a chart row behind it.

Found 2026-08-24 allocating DJLH Properties FY2025: searching the COA picker
for `1612` returned nothing, even though `1612 Development cost` was sitting
in that entity's FY2023 trial balance at 2,329,168.12. The allocation dropdown
is built from EntityChartOfAccount alone, so a code that reached the TB
without a chart row is invisible to it -- you can see the money and not be
able to allocate against it.

`commit_tb_import` has synced the chart since 2026-03-10 (30d6ccd), but it is
one of many writers. These do not sync, and each produced live orphans:

    entity_import_handiledger        never touches the chart
    _apply_journal_line_to_tb        only reads it, to resolve a name
    _populate_rolled_forward_fy      seeds the standard template only, so a
                                     custom code in the year being rolled is
                                     dropped and the orphan propagates yearly
    views_upgrades bulk import       no reference to the chart at all

There are 26 direct TrialBalanceLine.objects.create() calls across the
codebase, so the guarantee cannot live in any one caller. It belongs at the
point the row is written.
"""
from datetime import date
from decimal import Decimal

from django.test import TestCase

from core.models import (
    AccountMapping,
    Client as ClientModel,
    Entity,
    EntityChartOfAccount,
    FinancialYear,
    TrialBalanceLine,
)


class ChartRowFollowsEveryTrialBalanceLineTests(TestCase):
    def setUp(self):
        self.client_obj = ClientModel.objects.create(name="COA Sync Test Client")
        self.entity = Entity.objects.create(
            entity_name="Orphan Codes Pty Ltd",
            entity_type="company",
            client=self.client_obj,
        )
        self.fy = FinancialYear.objects.create(
            entity=self.entity, year_label="2025",
            start_date=date(2024, 7, 1), end_date=date(2025, 6, 30),
        )
        self.other_expenses = AccountMapping.objects.create(
            standard_code="ISEXP999", line_item_label="Other expenses",
            financial_statement="income_statement",
            statement_section="Expenses", display_order=900,
        )
        self.repairs = AccountMapping.objects.create(
            standard_code="ISEXP998", line_item_label="Repairs and maintenance",
            financial_statement="income_statement",
            statement_section="Expenses", display_order=901,
        )

    def _line(self, code, name="Development cost", **kw):
        return TrialBalanceLine.objects.create(
            financial_year=self.fy, account_code=code, account_name=name,
            closing_balance=Decimal("100.00"), **kw)

    def _chart(self, code):
        return EntityChartOfAccount.objects.filter(
            entity=self.entity, account_code=code).first()

    def test_a_tb_line_for_an_unknown_code_creates_the_chart_row(self):
        """The DJLH case: 1612 reached the TB and was never allocatable."""
        self.assertIsNone(self._chart("1612"))
        self._line("1612")
        row = self._chart("1612")
        self.assertIsNotNone(row)
        self.assertEqual(row.account_name, "Development cost")

    def test_the_section_is_inferred_from_the_code_range(self):
        self._line("1612", name="Development cost")
        self._line("620", name="Rents received")
        self.assertEqual(self._chart("1612").section,
                         EntityChartOfAccount.StatementSection.EXPENSES)
        self.assertEqual(self._chart("620").section,
                         EntityChartOfAccount.StatementSection.REVENUE)

    def test_non_current_codes_are_not_dumped_in_suspense(self):
        """_HL_RANGE_SECTION says 'Non Current Liabilities'; commit_tb_import's
        map keys that with a hyphen, so every non-current account fell through
        to SUSPENSE. 3546 is one of DJLH's related-party loans."""
        self._line("3546", name="Loan - Jim Penman")
        self._line("2800", name="7 Tinarra Court Kilsyth")
        self.assertEqual(self._chart("3546").section,
                         EntityChartOfAccount.StatementSection.LIABILITIES)
        self.assertEqual(self._chart("2800").section,
                         EntityChartOfAccount.StatementSection.ASSETS)

    def test_an_unreadable_code_lands_in_suspense(self):
        self._line("XYZ", name="Mystery account")
        self.assertEqual(self._chart("XYZ").section,
                         EntityChartOfAccount.StatementSection.SUSPENSE)

    def test_the_generated_row_is_marked_custom(self):
        """So it is distinguishable from a standard template account."""
        self._line("1612")
        self.assertTrue(self._chart("1612").is_custom)

    def test_an_existing_chart_row_keeps_its_name(self):
        """The chart is the source of truth for names. A TB file is untrusted
        input and must never rename an account an accountant set."""
        EntityChartOfAccount.objects.create(
            entity=self.entity, account_code="1612",
            account_name="Development costs (WIP)",
            section=EntityChartOfAccount.StatementSection.ASSETS,
            is_active=True)
        self._line("1612", name="DEVELOPMENT COST")
        row = self._chart("1612")
        self.assertEqual(row.account_name, "Development costs (WIP)")
        self.assertEqual(row.section, EntityChartOfAccount.StatementSection.ASSETS)

    def test_an_existing_chart_row_gets_a_missing_mapping_filled(self):
        EntityChartOfAccount.objects.create(
            entity=self.entity, account_code="1612",
            account_name="Development cost",
            section=EntityChartOfAccount.StatementSection.EXPENSES,
            maps_to=None, is_active=True)
        self._line("1612", mapped_line_item=self.other_expenses)
        self.assertEqual(self._chart("1612").maps_to, self.other_expenses)

    def test_an_existing_chart_row_keeps_the_mapping_it_has(self):
        EntityChartOfAccount.objects.create(
            entity=self.entity, account_code="1612",
            account_name="Development cost",
            section=EntityChartOfAccount.StatementSection.EXPENSES,
            maps_to=self.repairs, is_active=True)
        self._line("1612", mapped_line_item=self.other_expenses)
        self.assertEqual(self._chart("1612").maps_to, self.repairs)

    def test_a_new_row_carries_the_lines_mapping(self):
        self._line("1612", mapped_line_item=self.other_expenses)
        self.assertEqual(self._chart("1612").maps_to, self.other_expenses)

    def test_two_tb_lines_on_one_code_make_one_chart_row(self):
        """Journal adjustments legitimately create a second row per code."""
        self._line("1612")
        self._line("1612", is_adjustment=True)
        self.assertEqual(
            EntityChartOfAccount.objects.filter(
                entity=self.entity, account_code="1612").count(), 1)

    def test_editing_a_tb_line_creates_nothing(self):
        line = self._line("1612")
        EntityChartOfAccount.objects.filter(
            entity=self.entity, account_code="1612").delete()
        line.closing_balance = Decimal("250.00")
        line.save()
        self.assertIsNone(self._chart("1612"))
