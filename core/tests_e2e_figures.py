"""Tests for the canonical figures dump."""
import json

from django.test import TestCase

from core.e2e_figures import dump_figures
from core.e2e_fixture_data import FIXTURE_IDS, seed_fixture_entity
from core.models import FinancialYear


class DumpFiguresTests(TestCase):
    def setUp(self):
        seed_fixture_entity()
        self.prior = FinancialYear.objects.get(pk=FIXTURE_IDS["prior_fy"])
        self.current = FinancialYear.objects.get(pk=FIXTURE_IDS["current_fy"])

    def test_has_the_expected_top_level_sections(self):
        dump = dump_figures(self.prior)
        self.assertEqual(
            set(dump), {"trial_balance", "journals", "depreciation", "totals"}
        )

    def test_every_number_is_a_string(self):
        # Floats would make the golden file drift between machines, and a diff of
        # 12450.000000000002 vs 12450.0 is not a regression anyone wants to read.
        dump = dump_figures(self.prior)
        for row in dump["trial_balance"]:
            for key in ("debit", "credit", "closing_balance", "opening_balance"):
                self.assertIsInstance(row[key], str, f"{key} should be a string")

    def test_trial_balance_is_ordered_by_account_code(self):
        codes = [row["account_code"] for row in dump_figures(self.prior)["trial_balance"]]
        self.assertEqual(codes, sorted(codes))

    def test_is_json_serialisable_and_stable_across_calls(self):
        first = json.dumps(dump_figures(self.prior), sort_keys=True)
        second = json.dumps(dump_figures(self.prior), sort_keys=True)
        self.assertEqual(first, second)

    def test_totals_report_the_balance(self):
        totals = dump_figures(self.prior)["totals"]
        self.assertEqual(totals["debit"], "70000.00")
        self.assertEqual(totals["credit"], "70000.00")

    def test_depreciation_rows_come_from_the_year_being_dumped(self):
        self.assertEqual(len(dump_figures(self.current)["depreciation"]), 1)
        self.assertEqual(len(dump_figures(self.prior)["depreciation"]), 0)
