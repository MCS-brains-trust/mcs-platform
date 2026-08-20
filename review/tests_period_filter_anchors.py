"""A period-filtered import must not keep the whole statement's balances.

A CBA statement covering 1 May - 30 Jul 2026 was uploaded against a financial
year ending 30 June. The period filter dropped the July rows, but the opening
and closing balances still came from the statement header, which describes
every row it contains. Reconciliation had already run *before* the filter, so
it passed and logged "reconciled OK" while the figures being imported could
not possibly agree:

    opening 26,420.53 + kept movements +1,783.32 = 28,203.85
    reported closing                             = 14,001.89

Three different numbers, none of them matching. ``confirm_import`` then wrote
the stale pair onto the ReviewJob, so the mismatch outlived the upload.
"""
from django.test import SimpleTestCase

from .email_ingestion import rebase_anchors_for_kept_rows


def row(amount, balance=None, date="01/05/2026"):
    return {"date": date, "description": "row", "amount": amount,
            "balance": balance}


class NothingExcludedTests(SimpleTestCase):

    def test_anchors_are_left_alone_when_no_row_is_dropped(self):
        kept = [row(10.0, 110.0), row(-5.0, 105.0)]
        result = rebase_anchors_for_kept_rows(kept, 100.0, 105.0, 0)
        self.assertEqual(result["opening_balance"], 100.0)
        self.assertEqual(result["closing_balance"], 105.0)
        self.assertEqual(result["warning"], "")
        self.assertFalse(result["chain_broken"])

    def test_an_empty_kept_set_is_left_alone(self):
        result = rebase_anchors_for_kept_rows([], 100.0, 105.0, 3)
        self.assertEqual(result["opening_balance"], 100.0)
        self.assertEqual(result["warning"], "")


class PrintedBalancesTests(SimpleTestCase):
    """When rows carry the statement's own balance column, believe it."""

    def test_the_anchors_come_from_the_kept_rows_own_balances(self):
        kept = [row(10.0, 110.0), row(-5.0, 105.0)]
        result = rebase_anchors_for_kept_rows(kept, 100.0, 999.0, 4)
        self.assertEqual(result["opening_balance"], 100.0)
        self.assertEqual(result["closing_balance"], 105.0)

    def test_the_rebased_anchors_agree_with_the_kept_movements(self):
        kept = [row(10.0, 110.0), row(-5.0, 105.0)]
        result = rebase_anchors_for_kept_rows(kept, 100.0, 999.0, 4)
        movements = sum(t["amount"] for t in kept)
        self.assertAlmostEqual(
            result["opening_balance"] + movements,
            result["closing_balance"], places=2)

    def test_a_broken_chain_among_the_kept_rows_is_reported(self):
        kept = [row(10.0, 110.0), row(-5.0, 9999.0)]
        result = rebase_anchors_for_kept_rows(kept, 100.0, 999.0, 4)
        self.assertTrue(result["chain_broken"])
        self.assertIn("does not follow row to row", result["warning"])


class NoBalanceColumnTests(SimpleTestCase):
    """The geometry parser emits no per-row balance, so anchors are derived."""

    def test_a_dropped_tail_leaves_the_opening_and_moves_the_closing(self):
        kept = [row(10.0), row(-5.0)]
        result = rebase_anchors_for_kept_rows(kept, 100.0, 500.0, 7)
        self.assertEqual(result["opening_balance"], 100.0)
        self.assertEqual(result["closing_balance"], 105.0)

    def test_a_dropped_head_moves_the_opening_by_what_it_dropped(self):
        """Rows dropped before the first kept row have already moved the
        balance, so the surviving subset does not start where the statement
        does."""
        kept = [row(10.0), row(-5.0)]
        result = rebase_anchors_for_kept_rows(
            kept, 100.0, 500.0, 7, leading_excluded_total=-40.0)
        self.assertEqual(result["opening_balance"], 60.0)
        self.assertEqual(result["closing_balance"], 65.0)

    def test_the_derived_anchors_always_foot(self):
        kept = [row(10.0), row(-5.0), row(2.5)]
        result = rebase_anchors_for_kept_rows(
            kept, 100.0, 500.0, 7, leading_excluded_total=-40.0)
        movements = sum(t["amount"] for t in kept)
        self.assertAlmostEqual(
            result["opening_balance"] + movements,
            result["closing_balance"], places=2)


class WarningTests(SimpleTestCase):

    def _result(self):
        kept = [row(10.0), row(-5.0)]
        return rebase_anchors_for_kept_rows(
            kept, 100.0, 500.0, 77, "01/07/2026", "30/07/2026")

    def test_the_warning_counts_what_was_dropped(self):
        self.assertIn("77 transaction(s)", self._result()["warning"])

    def test_the_warning_names_the_dates_that_were_dropped(self):
        warning = self._result()["warning"]
        self.assertIn("01/07/2026", warning)
        self.assertIn("30/07/2026", warning)

    def test_the_warning_shows_both_pairs_of_figures(self):
        warning = self._result()["warning"]
        self.assertIn("100.00", warning)   # the statement's own
        self.assertIn("500.00", warning)
        self.assertIn("105.00", warning)   # what is actually being imported

    def test_a_single_excluded_date_reads_as_one_date(self):
        result = rebase_anchors_for_kept_rows(
            [row(10.0)], 100.0, 500.0, 1, "05/07/2026", "05/07/2026")
        self.assertIn("dated 05/07/2026", result["warning"])


class TheRealIncidentTests(SimpleTestCase):
    """The figures from the upload that exposed this, end to end."""

    def test_the_july_statement_anchors_are_corrected(self):
        # 163 rows kept (May and June), 77 July rows dropped, net -20,616.09.
        kept = [row(-20616.09)]
        result = rebase_anchors_for_kept_rows(
            kept, 26420.53, 14001.89, 77, "01/07/2026", "30/07/2026")
        self.assertAlmostEqual(result["opening_balance"], 26420.53, places=2)
        self.assertAlmostEqual(result["closing_balance"], 5804.44, places=2)
        self.assertAlmostEqual(
            result["opening_balance"] + sum(t["amount"] for t in kept),
            result["closing_balance"], places=2)

    def test_the_stale_closing_balance_is_gone(self):
        result = rebase_anchors_for_kept_rows(
            [row(-20616.09)], 26420.53, 14001.89, 77)
        self.assertNotAlmostEqual(
            result["closing_balance"], 14001.89, places=2)
