"""
Backfill for trial balance rows an import renamed.

The forward fix (integrations/views.py) stops new imports writing a source
name against a chart code. The rows already written keep the wrong name
until this command rewrites them, so it carries the same rule: where the
entity chart has an entry for the row's code, the chart's name wins.

Two guards matter. Rows whose code has no chart entry are left alone —
there is no authoritative name to apply and the orphan-code backfill is a
separate piece of work. And finalised years are skipped unless explicitly
asked for, because renaming an account in a finalised year changes
financial statements that have already been issued.
"""

from datetime import date
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from core.models import (
    Entity,
    EntityChartOfAccount,
    FinancialYear,
    TrialBalanceLine,
)


class BackfillTbAccountNamesTests(TestCase):
    def setUp(self):
        self.entity = Entity.objects.create(entity_name="Minli Enterprise Unit Trust")
        EntityChartOfAccount.objects.create(
            entity=self.entity,
            account_code="620",
            account_name="Rents received",
            section="revenue",
        )
        EntityChartOfAccount.objects.create(
            entity=self.entity,
            account_code="2000",
            account_name="Cash at bank",
            section="current_assets",
        )

    def _fy(self, year, status="draft"):
        return FinancialYear.objects.create(
            entity=self.entity,
            start_date=date(year - 1, 7, 1),
            end_date=date(year, 6, 30),
            status=status,
        )

    def _line(self, fy, code, name, source="tb_import"):
        return TrialBalanceLine.objects.create(
            financial_year=fy,
            account_code=code,
            account_name=name,
            source=source,
        )

    def _run(self, *args):
        out = StringIO()
        call_command("backfill_tb_account_names", *args, stdout=out)
        return out.getvalue()

    def test_dry_run_reports_the_row_but_changes_nothing(self):
        fy = self._fy(2026)
        line = self._line(fy, "620", "Rental Income")

        output = self._run("--dry-run")

        line.refresh_from_db()
        self.assertEqual(line.account_name, "Rental Income")
        self.assertIn("Rental Income", output)
        self.assertIn("Rents received", output)

    def test_rewrites_the_import_name_to_the_chart_name(self):
        fy = self._fy(2026)
        line = self._line(fy, "620", "Rental Income")

        self._run()

        line.refresh_from_db()
        self.assertEqual(line.account_name, "Rents received")

    def test_rollover_and_journal_rows_are_corrected_too(self):
        # The wrong name reached these rows by being copied forward, so
        # they carry it just as the import rows do.
        fy = self._fy(2026)
        rollover = self._line(fy, "2000", "CBA trading account", source="rollover")
        journal = self._line(fy, "2000", "PENMAN PROPERTY", source="manual_journal")

        self._run()

        rollover.refresh_from_db()
        journal.refresh_from_db()
        self.assertEqual(rollover.account_name, "Cash at bank")
        self.assertEqual(journal.account_name, "Cash at bank")

    def test_code_with_no_chart_entry_is_left_alone(self):
        fy = self._fy(2026)
        orphan = self._line(fy, "9999", "Some Xero account")

        self._run()

        orphan.refresh_from_db()
        self.assertEqual(orphan.account_name, "Some Xero account")

    def test_finalised_years_are_skipped_by_default(self):
        fy = self._fy(2025, status="finalised")
        line = self._line(fy, "620", "Rental Income")

        self._run()

        line.refresh_from_db()
        self.assertEqual(line.account_name, "Rental Income")

    def test_finalised_years_are_corrected_when_asked_for(self):
        fy = self._fy(2025, status="finalised")
        line = self._line(fy, "620", "Rental Income")

        self._run("--include-finalised")

        line.refresh_from_db()
        self.assertEqual(line.account_name, "Rents received")

    def test_entity_filter_limits_the_blast_radius(self):
        other = Entity.objects.create(entity_name="Berwick Mechanical Services")
        EntityChartOfAccount.objects.create(
            entity=other, account_code="620",
            account_name="Rents received", section="revenue",
        )
        other_fy = FinancialYear.objects.create(
            entity=other, start_date=date(2025, 7, 1), end_date=date(2026, 6, 30),
        )
        other_line = TrialBalanceLine.objects.create(
            financial_year=other_fy, account_code="620",
            account_name="Rental Income", source="tb_import",
        )
        mine = self._line(self._fy(2026), "620", "Rental Income")

        self._run("--entity", "Minli")

        mine.refresh_from_db()
        other_line.refresh_from_db()
        self.assertEqual(mine.account_name, "Rents received")
        self.assertEqual(other_line.account_name, "Rental Income")
