"""Nothing whose own figures contradict it may become an import unnoticed.

Before this gate, the only balance check ran on the *parse*, before the
financial-year filter, and a statement with no readable balances was waved
through as merely "unverified" -- a yellow banner on a preview page that
``confirm_import`` never consulted.

Both gaps were load-bearing in one real upload. A CBA statement covering
1 May - 30 Jul 2026 reconciled across all 240 of its rows, so the parse check
passed. The filter then dropped 130 rows for a 30 June year end, and the 110
that were imported still carried the whole statement's 26,420.53 -> 14,001.89.
Separately, 53 of the dropped rows had been dated a year early by an OCR chunk
that could not see the year, which no balance check can detect because only the
dates were wrong.
"""
from django.test import SimpleTestCase

from .statement_geometry import StatementNotImportable, assert_importable


def row(amount, balance=None, date="01/05/2026", description="row"):
    return {"date": date, "description": description, "amount": amount,
            "balance": balance}


class CleanStatementTests(SimpleTestCase):

    def test_a_statement_that_adds_up_is_allowed(self):
        rows = [row(10.0, 110.0), row(-5.0, 105.0)]
        self.assertTrue(assert_importable(rows, 100.0, 105.0))

    def test_a_statement_with_no_balance_column_is_allowed_if_it_foots(self):
        """Not every parser emits a per-row balance. Absence of the column is
        not evidence of a fault -- the totals still have to agree."""
        rows = [row(10.0), row(-5.0)]
        self.assertTrue(assert_importable(rows, 100.0, 105.0))

    def test_an_empty_row_set_is_not_the_gate_s_business(self):
        self.assertTrue(assert_importable([], None, None))

    def test_unparseable_dates_do_not_count_as_disorder(self):
        rows = [row(10.0, 110.0, date=""), row(-5.0, 105.0, date="n/a")]
        self.assertTrue(assert_importable(rows, 100.0, 105.0))


class MissingAnchorTests(SimpleTestCase):

    def test_a_statement_with_no_balances_at_all_is_refused(self):
        with self.assertRaises(StatementNotImportable) as caught:
            assert_importable([row(10.0)], None, None)
        self.assertIn("No opening or closing balance", caught.exception.reason)

    def test_zero_balances_count_as_no_balances(self):
        """The text parsers default both anchors to 0, so 0/0 is how a failed
        anchor read actually presents."""
        with self.assertRaises(StatementNotImportable):
            assert_importable([row(10.0)], 0, 0)

    def test_one_balance_is_enough_to_check_against(self):
        self.assertTrue(assert_importable([row(10.0)], 0, 10.0))


class ReconciliationTests(SimpleTestCase):

    def test_rows_that_do_not_add_up_to_the_balances_are_refused(self):
        with self.assertRaises(StatementNotImportable) as caught:
            assert_importable([row(10.0)], 100.0, 999.0)
        self.assertIn("do not add up", caught.exception.reason)

    def test_the_refusal_names_the_shortfall(self):
        with self.assertRaises(StatementNotImportable) as caught:
            assert_importable([row(10.0)], 100.0, 999.0)
        self.assertIn("889.00", caught.exception.reason)

    def test_a_cent_of_rounding_is_tolerated(self):
        self.assertTrue(assert_importable([row(10.0)], 100.0, 110.005))


class ChainTests(SimpleTestCase):

    def test_a_broken_running_balance_is_refused(self):
        """Totals can foot over a broken chain -- drop one row and duplicate
        another of the same value and the sum is unchanged -- so the chain is
        checked in its own right."""
        rows = [row(10.0, 110.0), row(10.0, 9999.0), row(-10.0, 110.0)]
        with self.assertRaises(StatementNotImportable) as caught:
            assert_importable(rows, 100.0, 110.0)
        self.assertIn("does not follow row to row", caught.exception.reason)

    def test_the_refusal_points_at_the_first_bad_row(self):
        # The totals foot (100 + 20 = 120), so the run reaches the chain check
        # rather than stopping at reconciliation.
        rows = [row(10.0, 110.0), row(10.0, 9999.0, description="TELSTRA")]
        with self.assertRaises(StatementNotImportable) as caught:
            assert_importable(rows, 100.0, 120.0)
        self.assertIn("TELSTRA", caught.exception.reason)


class DateOrderTests(SimpleTestCase):

    def test_dates_running_backwards_are_refused(self):
        """The wrong-year OCR chunk: balances chain perfectly, dates do not."""
        rows = [
            row(10.0, 110.0, date="02/06/2026"),
            row(10.0, 120.0, date="05/06/2025"),
            row(10.0, 130.0, date="22/06/2026"),
        ]
        with self.assertRaises(StatementNotImportable) as caught:
            assert_importable(rows, 100.0, 130.0)
        self.assertIn("run backwards", caught.exception.reason)

    def test_the_balances_alone_would_not_have_caught_it(self):
        """Guard the premise: if the chain caught this, the date check would be
        redundant and the test above would prove nothing."""
        rows = [
            row(10.0, 110.0, date="02/06/2026"),
            row(10.0, 120.0, date="05/06/2025"),
        ]
        from .statement_geometry import _chain_break_indexes
        self.assertEqual(_chain_break_indexes(rows, 100.0), [])

    def test_a_statement_in_order_passes(self):
        rows = [
            row(10.0, 110.0, date="02/06/2026"),
            row(10.0, 120.0, date="05/06/2026"),
        ]
        self.assertTrue(assert_importable(rows, 100.0, 120.0))


class TheRealIncidentTests(SimpleTestCase):

    def test_the_july_import_would_have_been_refused(self):
        """The exact shape that got through: the surviving rows carrying the
        whole statement's anchors."""
        rows = [row(1783.32)]          # net movement of the 110 rows imported
        with self.assertRaises(StatementNotImportable) as caught:
            assert_importable(rows, 26420.53, 14001.89)
        self.assertIn("do not add up", caught.exception.reason)

    def test_the_corrected_figures_are_accepted(self):
        """And once the anchors are rebased to the rows actually kept, the same
        import passes -- the gate objects to the mismatch, not to filtering."""
        rows = [row(-20616.09)]
        self.assertTrue(assert_importable(rows, 26420.53, 5804.44))
