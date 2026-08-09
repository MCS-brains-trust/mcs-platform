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

    def test_tied_journals_order_by_pk_not_insertion_order(self):
        # depreciation_post_to_tb() reverses and re-posts on every call, so a year
        # can end up holding several journals that share journal_type, the fixed
        # year-end journal_date, and a description built from a fixed template —
        # exactly the tie the brief's three ordering keys can't break on their own.
        # A later task's idempotency check dumps figures after a first post and
        # again after a second post and diffs them, so this tie has to resolve the
        # same way every time regardless of which journal the DB happened to
        # create first.
        #
        # Insert the journal with the *lower* pk second, so "creation order" and
        # "pk order" actively disagree. If dump_figures ever loses its pk
        # tie-break, ties fall back to something like insertion order and the
        # assertion below flips — this is the regression a silent order_by edit
        # would otherwise sail through.
        from core.models import AdjustingJournal

        tied_fields = dict(
            financial_year=self.current,
            journal_type=AdjustingJournal.JournalType.DEPRECIATION,
            journal_date=self.current.end_date,
            description="Depreciation for year ended tie test",
        )
        AdjustingJournal.objects.create(
            pk="e2e00000-0000-4000-8000-0000000000b1",
            total_debit="111.11",
            total_credit="111.11",
            **tied_fields,
        )
        AdjustingJournal.objects.create(
            pk="e2e00000-0000-4000-8000-0000000000a1",
            total_debit="222.22",
            total_credit="222.22",
            **tied_fields,
        )

        journals = dump_figures(self.current)["journals"]
        tied = [j for j in journals if j["description"] == "Depreciation for year ended tie test"]
        # Lower pk ("...a1") sorts first even though it was created second.
        self.assertEqual([j["total_debit"] for j in tied], ["222.22", "111.11"])
