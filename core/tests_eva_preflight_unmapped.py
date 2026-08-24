"""Eva's pre-flight must see unmapped journal lines.

Found 2026-08-24 testing DJLH Properties FY2025 before finalisation. Two
trial-balance lines carrying 880,548.32 between them had no statement-line
mapping, so both were silently dropped from the P&L -- net profit read
-157,473.55 instead of -495,831.23. Eva's pre-flight was asked first and
answered "All accounts are mapped."

It could not see them. The check excluded is_adjustment=True, and both lines
came from manual journals, so the one gate meant to catch unmapped balances
before review counted 0 where the answer was 2. financial_year_finalise_full
has no unmapped check of its own, so nothing else would have stopped it.

An unmapped line is dropped from the statements whether a journal made it or
an import did -- _calculate_net_profit (core/views.py) and
fs_template_service both key on mapped_line_item. So the flag the row was
created under cannot be what decides whether it is worth reporting.
"""
from datetime import date
from decimal import Decimal

from django.test import TestCase

from core.eva_engine import run_preflight_checks
from core.models import (
    AccountMapping,
    Client as ClientModel,
    Entity,
    FinancialYear,
    TrialBalanceLine,
)


def _check(result, name):
    return next(c for c in result["checks"] if c["name"] == name)


class PreflightSeesUnmappedJournalLinesTests(TestCase):
    def setUp(self):
        self.client_obj = ClientModel.objects.create(name="Preflight Test Client")
        self.entity = Entity.objects.create(
            entity_name="Preflight Pty Ltd", entity_type="company",
            client=self.client_obj, abn="97627730394")
        self.fy = FinancialYear.objects.create(
            entity=self.entity, year_label="2025",
            start_date=date(2024, 7, 1), end_date=date(2025, 6, 30))
        self.revenue = AccountMapping.objects.create(
            standard_code="ISREV900", line_item_label="Interest received",
            financial_statement="income_statement",
            statement_section="Revenue", display_order=10)

    def _line(self, code, name, mapping=None, is_adjustment=False):
        return TrialBalanceLine.objects.create(
            financial_year=self.fy, account_code=code, account_name=name,
            debit=Decimal("0"), credit=Decimal("271095.32"),
            closing_balance=Decimal("-271095.32"),
            mapped_line_item=mapping, is_adjustment=is_adjustment)

    def test_an_unmapped_journal_line_is_reported(self):
        """The DJLH case: created by a manual journal, so is_adjustment=True."""
        self._line("0575", "Interest received", mapping=None, is_adjustment=True)
        check = _check(run_preflight_checks(self.fy), "All accounts mapped")
        self.assertFalse(check["passed"])
        self.assertIn("1 account", check["message"])

    def test_an_unmapped_imported_line_is_still_reported(self):
        self._line("0575", "Interest received", mapping=None, is_adjustment=False)
        check = _check(run_preflight_checks(self.fy), "All accounts mapped")
        self.assertFalse(check["passed"])

    def test_a_mapped_journal_line_is_not_reported(self):
        self._line("575", "Interest received", mapping=self.revenue,
                   is_adjustment=True)
        check = _check(run_preflight_checks(self.fy), "All accounts mapped")
        self.assertTrue(check["passed"])
        self.assertEqual(check["message"], "All accounts are mapped.")

    def test_both_kinds_are_counted_together(self):
        self._line("0575", "Interest received", is_adjustment=True)
        self._line("0550", "Dividends - Franked", is_adjustment=False)
        check = _check(run_preflight_checks(self.fy), "All accounts mapped")
        self.assertIn("2 account", check["message"])

    def test_an_unmapped_journal_line_fails_the_whole_preflight(self):
        """It must gate, not just annotate — this is what precedes finalising.

        The trial balance is deliberately balanced and the ABN set, so the
        mapping check is the only one that can fail. Without that the test
        passes on an unbalanced fixture and proves nothing.
        """
        self._line("0575", "Interest received", is_adjustment=True)
        TrialBalanceLine.objects.create(
            financial_year=self.fy, account_code="3565",
            account_name="Loan - Li Penman", debit=Decimal("271095.32"),
            credit=Decimal("0"), closing_balance=Decimal("271095.32"),
            mapped_line_item=self.revenue, is_adjustment=True)

        result = run_preflight_checks(self.fy)

        self.assertTrue(_check(result, "Trial balance is balanced")["passed"])
        self.assertTrue(_check(result, "ABN recorded")["passed"])
        self.assertFalse(_check(result, "All accounts mapped")["passed"])
        self.assertFalse(result["passed"])
