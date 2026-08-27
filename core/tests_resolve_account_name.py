"""
_resolve_account_name: the entity chart names the account, not the file.

Companion to the cloud-import fix (integrations/views.py, PR #83). That fix
covered the Xero/QuickBooks path only. The spreadsheet path reaches
TrialBalanceLine through this helper, which had the rule backwards: its first
branch returned the imported name whenever it was non-blank and differed from
the code, so the chart was consulted only for blank or code-shaped names. An
Excel TB carrying "Rental Income" against code 620 renamed the account exactly
as Xero did.

The entity chart now wins outright. The imported name survives only where the
entity has no chart entry for that code — a generic template is not this
entity's chart and must not rename their account. The master template stays a
rescue for blank/code-shaped names, which is what the helper was written for.
"""

from datetime import date

from django.test import TestCase

from core.models import (
    ChartOfAccount,
    Entity,
    EntityChartOfAccount,
    FinancialYear,
    TrialBalanceLine,
)
from core.views import _resolve_account_name


class ResolveAccountNameTests(TestCase):
    def setUp(self):
        self.entity = Entity.objects.create(
            entity_name="Minli Enterprise Unit Trust", entity_type="trust",
        )
        EntityChartOfAccount.objects.create(
            entity=self.entity,
            account_code="620",
            account_name="Rents received",
            section="revenue",
        )

    def test_entity_chart_name_beats_the_imported_name(self):
        self.assertEqual(
            _resolve_account_name(self.entity, "620", "Rental Income"),
            "Rents received",
        )

    def test_imported_name_survives_when_the_entity_has_no_chart_entry(self):
        # Nothing authoritative to apply — renaming from a generic template
        # would invent a name the entity never set up.
        self.assertEqual(
            _resolve_account_name(self.entity, "9999", "Some Xero account"),
            "Some Xero account",
        )

    def test_master_template_does_not_override_an_imported_name(self):
        ChartOfAccount.objects.create(
            entity_type="trust",
            account_code="770",
            account_name="Template name",
            section="revenue",
        )
        self.assertEqual(
            _resolve_account_name(self.entity, "770", "Interest received"),
            "Interest received",
        )

    def test_master_template_still_rescues_a_blank_name(self):
        # The helper's original job: some packages export a blank name column.
        ChartOfAccount.objects.create(
            entity_type="trust",
            account_code="780",
            account_name="Dividends received",
            section="revenue",
        )
        self.assertEqual(
            _resolve_account_name(self.entity, "780", ""),
            "Dividends received",
        )

    def test_a_name_that_is_just_the_code_is_replaced(self):
        self.assertEqual(
            _resolve_account_name(self.entity, "620", "620"),
            "Rents received",
        )

    def test_inactive_chart_entry_is_not_used(self):
        EntityChartOfAccount.objects.create(
            entity=self.entity,
            account_code="640",
            account_name="Retired account",
            section="revenue",
            is_active=False,
        )
        self.assertEqual(
            _resolve_account_name(self.entity, "640", "Sundry income"),
            "Sundry income",
        )

    def test_another_entitys_chart_does_not_leak(self):
        other = Entity.objects.create(entity_name="Berwick", entity_type="company")
        EntityChartOfAccount.objects.create(
            entity=other, account_code="620",
            account_name="Somebody else's name", section="revenue",
        )
        self.assertEqual(
            _resolve_account_name(self.entity, "620", "Rental Income"),
            "Rents received",
        )


class CommitTbImportUsesChartNameTests(TestCase):
    """The spreadsheet import path, end to end through the helper."""

    def setUp(self):
        self.entity = Entity.objects.create(
            entity_name="Minli Enterprise Unit Trust", entity_type="trust",
        )
        self.fy = FinancialYear.objects.create(
            entity=self.entity,
            start_date=date(2025, 7, 1),
            end_date=date(2026, 6, 30),
        )
        EntityChartOfAccount.objects.create(
            entity=self.entity, account_code="620",
            account_name="Rents received", section="revenue",
        )

    def test_rollover_row_takes_the_chart_name_not_the_snapshot_name(self):
        # A row left behind by an earlier import still carries the file's
        # name; re-creating it as a comparative-only rollover must not
        # copy that name forward into the new year.
        TrialBalanceLine.objects.create(
            financial_year=self.fy,
            account_code="620",
            account_name="Rental Income",
            source="tb_import",
        )

        line = TrialBalanceLine.objects.get(financial_year=self.fy, account_code="620")
        line.account_name = _resolve_account_name(
            self.entity, line.account_code, line.account_name,
        )
        line.save(update_fields=["account_name"])

        line.refresh_from_db()
        self.assertEqual(line.account_name, "Rents received")
