"""Tests for the balance-chain self-verification in Vision OCR extraction.

A bank statement prints a running balance beside every transaction. Chaining
it is the only way an extraction can notice it dropped a row or read one
twice — the model has no way to know either happened. The real scanned ANZ
statement came back with 6 duplicated rows and a missing July tail, and
nothing in the extraction could tell.

Client mocked throughout; PDFs synthesised by pypdf. No API budget spent.
"""
from django.test import TestCase

from .email_ingestion import (
    _chain_breaks,
    _merge_chunk_results,
    extract_transactions_from_pdf,
)
from .tests_vision_chunking import (
    VisionMock,
    chunk_payload,
    make_pdf_b64,
    txn,
    vision_response,
)


def rows_payload(rows):
    """A chunk payload carrying an explicit list of transactions."""
    return {**chunk_payload([]), "transactions": rows}


class ChainBreakTests(TestCase):

    def test_a_clean_chain_reports_no_breaks(self):
        rows = [txn("a", 100.0, 1100.0), txn("b", -50.0, 1050.0)]

        self.assertEqual(_chain_breaks(rows, opening=1000.0), [])

    def test_a_dropped_row_breaks_the_chain(self):
        # The -50 row was never extracted, so c's balance cannot follow a's.
        rows = [txn("a", 100.0, 1100.0), txn("c", 25.0, 1075.0)]

        self.assertEqual(_chain_breaks(rows, opening=1000.0), [1])

    def test_a_duplicated_row_breaks_the_chain(self):
        rows = [txn("a", 100.0, 1100.0), txn("a", 100.0, 1100.0)]

        self.assertEqual(_chain_breaks(rows, opening=1000.0), [1])

    def test_rows_without_a_balance_column_are_not_treated_as_broken(self):
        rows = [txn("a", 100.0), txn("b", -50.0)]

        self.assertEqual(_chain_breaks(rows, opening=1000.0), [])

    def test_a_chain_is_checked_even_with_no_opening_anchor(self):
        rows = [txn("a", 100.0, 1100.0), txn("c", 25.0, 1075.0)]

        self.assertEqual(_chain_breaks(rows, opening=None), [1])

    def test_cents_rounding_does_not_count_as_a_break(self):
        rows = [txn("a", 0.1, 1000.1), txn("b", 0.2, 1000.3)]

        self.assertEqual(_chain_breaks(rows, opening=1000.0), [])


class ChunkJoinTests(TestCase):

    def test_a_row_repeated_across_a_chunk_join_is_kept_once(self):
        merged = _merge_chunk_results([
            rows_payload([txn("a", 100.0, 1100.0), txn("b", -50.0, 1050.0)]),
            rows_payload([txn("b", -50.0, 1050.0), txn("c", 25.0, 1075.0)]),
        ])

        self.assertEqual(
            [t["description"] for t in merged["transactions"]], ["a", "b", "c"])
        self.assertFalse(merged.get("chain_broken"))

    def test_several_repeated_rows_across_a_join_are_all_dropped(self):
        merged = _merge_chunk_results([
            rows_payload([txn("a", 100.0, 1100.0), txn("b", -50.0, 1050.0)]),
            rows_payload([txn("a", 100.0, 1100.0), txn("b", -50.0, 1050.0),
                          txn("c", 25.0, 1075.0)]),
        ])

        self.assertEqual(
            [t["description"] for t in merged["transactions"]], ["a", "b", "c"])

    def test_a_gap_between_chunks_is_flagged_rather_than_hidden(self):
        merged = _merge_chunk_results([
            rows_payload([txn("a", 100.0, 1100.0)]),
            rows_payload([txn("z", 25.0, 5075.0)]),
        ])

        self.assertTrue(merged.get("chain_broken"))

    def test_a_clean_run_across_chunks_is_not_flagged(self):
        merged = _merge_chunk_results([
            rows_payload([txn("a", 100.0, 1100.0)]),
            rows_payload([txn("b", 25.0, 1125.0)]),
        ])

        self.assertFalse(merged.get("chain_broken"))

    def test_opening_and_closing_are_derived_from_the_chain(self):
        merged = _merge_chunk_results([
            rows_payload([txn("a", 100.0, 1100.0)]),
            rows_payload([txn("b", 25.0, 1125.0)]),
        ])

        self.assertEqual(merged["opening_balance"], 1000.0)
        self.assertEqual(merged["closing_balance"], 1125.0)

    def test_a_statement_with_no_balance_column_keeps_the_reported_anchors(self):
        merged = _merge_chunk_results([
            chunk_payload([0], opening_balance=500.0),
            chunk_payload([1], closing_balance=520.0),
        ])

        self.assertEqual(merged["opening_balance"], 500.0)
        self.assertEqual(merged["closing_balance"], 520.0)
        self.assertFalse(merged.get("chain_broken"))


class ChainRetryTests(TestCase):

    def test_a_chunk_with_a_broken_chain_is_re_extracted(self):
        attempts = {"n": 0}

        def broken_then_clean(pages):
            attempts["n"] += 1
            if attempts["n"] == 1:
                return vision_response(rows_payload([
                    txn("a", 100.0, 1100.0), txn("c", 25.0, 1075.0)]))
            return vision_response(rows_payload([
                txn("a", 100.0, 1100.0), txn("b", -50.0, 1050.0),
                txn("c", 25.0, 1075.0)]))

        mock = VisionMock(responder=broken_then_clean)

        with mock.install():
            result = extract_transactions_from_pdf(make_pdf_b64(2), "ANZ.pdf")

        self.assertEqual(len(mock.calls), 2)
        self.assertEqual(
            [t["description"] for t in result["transactions"]], ["a", "b", "c"])
        self.assertFalse(result.get("chain_broken"))

    def test_a_chain_still_broken_after_retry_flags_rather_than_fails(self):
        # Partial data the reviewer can see and check beats a refused upload.
        mock = VisionMock(
            responder=lambda pages: vision_response(rows_payload([
                txn("a", 100.0, 1100.0), txn("c", 25.0, 1075.0)]))
        )

        with mock.install():
            result = extract_transactions_from_pdf(make_pdf_b64(2), "ANZ.pdf")

        self.assertTrue(result["chain_broken"])
        self.assertEqual(len(result["transactions"]), 2)

    def test_a_clean_chunk_is_never_re_extracted(self):
        mock = VisionMock(
            responder=lambda pages: vision_response(rows_payload([
                txn("a", 100.0, 1100.0), txn("b", -50.0, 1050.0)]))
        )

        with mock.install():
            extract_transactions_from_pdf(make_pdf_b64(2), "ANZ.pdf")

        self.assertEqual(len(mock.calls), 1)
