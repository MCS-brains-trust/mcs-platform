"""A broken chain should say where the statement jumps, not just that it did.

The scanned ANZ exemplar turned out to be missing the pages covering 1-19
July: page 21 ends on 30/06 and page 22 opens on 20/07 with the balance
already 5,514.81 higher. No extractor can recover rows that are not in the
file, so the useful thing is to name the gap and let someone fetch the
missing page.
"""
from django.test import TestCase

from .email_ingestion import _describe_chain_gaps
from .tests_vision_chunking import txn


class DescribeChainGapsTests(TestCase):

    def test_a_gap_names_the_dates_and_the_amount(self):
        rows = [
            txn("a", -120.11, 2155.33, date="30/06/2026"),
            txn("b", -211.30, 7458.84, date="20/07/2026"),
        ]

        description = _describe_chain_gaps(rows, [1])

        self.assertIn("30/06/2026", description)
        self.assertIn("20/07/2026", description)
        self.assertIn("5,514.81", description)

    def test_no_breaks_describes_nothing(self):
        rows = [txn("a", 100.0, 1100.0), txn("b", -50.0, 1050.0)]

        self.assertEqual(_describe_chain_gaps(rows, []), "")

    def test_several_gaps_are_all_named(self):
        rows = [
            txn("a", 10.0, 110.0, date="01/07/2025"),
            txn("b", 10.0, 500.0, date="02/07/2025"),
            txn("c", 10.0, 900.0, date="03/07/2025"),
        ]

        description = _describe_chain_gaps(rows, [1, 2])

        self.assertIn("02/07/2025", description)
        self.assertIn("03/07/2025", description)

    def test_a_break_at_the_first_row_is_described_without_a_predecessor(self):
        rows = [txn("a", 10.0, 900.0, date="01/07/2025")]

        description = _describe_chain_gaps(rows, [0])

        self.assertIn("01/07/2025", description)
