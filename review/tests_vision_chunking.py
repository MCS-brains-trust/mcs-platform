"""Tests for the chunked Claude Vision OCR fallback.

A scanned bank statement is image-only: no text parser can touch it, so the
whole statement goes to Vision. A 24-page scan needs roughly 48,000 output
tokens, well past any single response cap, so the PDF is split into page
chunks and each chunk is extracted on its own call.

Every test here mocks the Anthropic client. None of them spends API budget or
depends on a client PDF — the PDFs are synthesised in-process by pypdf, with
each page given a distinct width so a mocked call can tell which pages it was
handed.
"""
import base64
import io
import json
import time
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase

from pypdf import PdfReader, PdfWriter

from .email_ingestion import (
    VisionExtractionError,
    _merge_chunk_results,
    _split_pdf_pages,
    extract_transactions_from_pdf,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PAGE_WIDTH_BASE = 200
PAGE_HEIGHT = 800


def make_pdf_b64(n_pages):
    """Build an ``n_pages`` PDF where page *i* is ``PAGE_WIDTH_BASE + i`` wide.

    The width encodes the page index, so a mocked Vision call can recover which
    slice of the statement it was given without needing real page content.
    """
    writer = PdfWriter()
    for i in range(n_pages):
        writer.add_blank_page(width=PAGE_WIDTH_BASE + i, height=PAGE_HEIGHT)
    buf = io.BytesIO()
    writer.write(buf)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def pdf_page_indexes(pdf_bytes):
    """Recover the original page indexes carried by a chunk's page widths."""
    reader = PdfReader(io.BytesIO(pdf_bytes))
    return [int(round(float(p.mediabox.width))) - PAGE_WIDTH_BASE for p in reader.pages]


def chunk_page_indexes_from_call(kwargs):
    """Recover page indexes from the document block of a messages.create call."""
    for block in kwargs["messages"][0]["content"]:
        if block["type"] == "document":
            return pdf_page_indexes(base64.b64decode(block["source"]["data"]))
    raise AssertionError("no document block in the request")


def vision_response(payload, stop_reason="end_turn"):
    """A stand-in for an Anthropic Message carrying a single JSON text block."""
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=json.dumps(payload))],
        stop_reason=stop_reason,
    )


def chunk_payload(page_indexes, **overrides):
    """A plausible per-chunk extraction result, one transaction per page."""
    payload = {
        "opening_balance": None,
        "closing_balance": None,
        "account_name": "",
        "bsb": "",
        "account_number": "",
        "period_start": "",
        "period_end": "",
        "transactions": [
            {"date": "01/07/2025", "description": f"page-{i}", "amount": 10.0}
            for i in page_indexes
        ],
    }
    payload.update(overrides)
    return payload


class _StreamContext:
    """Stands in for the SDK's streaming context manager."""

    def __init__(self, message):
        self._message = message

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self._message


class VisionMock:
    """Records every messages.stream call and answers it per page range."""

    def __init__(self, responder=None):
        self.calls = []
        self.responder = responder or (
            lambda pages: vision_response(chunk_payload(pages))
        )

    def __call__(self, **kwargs):
        pages = chunk_page_indexes_from_call(kwargs)
        self.calls.append({"pages": pages, "kwargs": kwargs})
        return _StreamContext(self.responder(pages))

    def install(self):
        """Patch the client factory so messages.stream routes here."""
        client = SimpleNamespace(messages=SimpleNamespace(stream=self))
        return patch(
            "review.email_ingestion._get_anthropic_client", return_value=client
        )

    @property
    def page_ranges(self):
        return [c["pages"] for c in self.calls]


# ---------------------------------------------------------------------------
# Splitting
# ---------------------------------------------------------------------------

class SplitPdfPagesTests(TestCase):

    def test_24_page_statement_splits_into_6_chunks_of_4_pages(self):
        pdf_bytes = base64.b64decode(make_pdf_b64(24))

        chunks = _split_pdf_pages(pdf_bytes, 4)

        self.assertEqual(
            [pdf_page_indexes(c) for c in chunks],
            [[0, 1, 2, 3], [4, 5, 6, 7], [8, 9, 10, 11],
             [12, 13, 14, 15], [16, 17, 18, 19], [20, 21, 22, 23]],
        )

    def test_trailing_partial_chunk_keeps_the_remaining_pages(self):
        pdf_bytes = base64.b64decode(make_pdf_b64(18))

        chunks = _split_pdf_pages(pdf_bytes, 4)

        self.assertEqual(len(chunks), 5)
        self.assertEqual(pdf_page_indexes(chunks[-1]), [16, 17])

    def test_statement_shorter_than_one_chunk_is_a_single_chunk(self):
        pdf_bytes = base64.b64decode(make_pdf_b64(3))

        chunks = _split_pdf_pages(pdf_bytes, 4)

        self.assertEqual(len(chunks), 1)
        self.assertEqual(pdf_page_indexes(chunks[0]), [0, 1, 2])


# ---------------------------------------------------------------------------
# Merging
# ---------------------------------------------------------------------------

class MergeChunkResultsTests(TestCase):

    def test_transactions_are_concatenated_in_chunk_order(self):
        merged = _merge_chunk_results([
            chunk_payload([0, 1]),
            chunk_payload([2, 3]),
            chunk_payload([4]),
        ])

        self.assertEqual(
            [t["description"] for t in merged["transactions"]],
            ["page-0", "page-1", "page-2", "page-3", "page-4"],
        )

    def test_opening_balance_comes_from_the_first_chunk_reporting_one(self):
        merged = _merge_chunk_results([
            chunk_payload([0], opening_balance=1000.0),
            chunk_payload([1], opening_balance=2000.0),
        ])

        self.assertEqual(merged["opening_balance"], 1000.0)

    def test_closing_balance_comes_from_the_last_chunk_reporting_one(self):
        merged = _merge_chunk_results([
            chunk_payload([0], closing_balance=1000.0),
            chunk_payload([1], closing_balance=2000.0),
            chunk_payload([2]),
        ])

        self.assertEqual(merged["closing_balance"], 2000.0)

    def test_header_fields_come_from_the_first_chunk_carrying_them(self):
        merged = _merge_chunk_results([
            chunk_payload([0], account_name="", bsb=""),
            chunk_payload([1], account_name="Hazaway Trust", bsb="082-123",
                          account_number="12345678"),
            chunk_payload([2], account_name="Wrong Later Guess", bsb="999-999"),
        ])

        self.assertEqual(merged["account_name"], "Hazaway Trust")
        self.assertEqual(merged["bsb"], "082-123")
        self.assertEqual(merged["account_number"], "12345678")

    def test_period_runs_from_the_first_start_to_the_last_end(self):
        merged = _merge_chunk_results([
            chunk_payload([0], period_start="01/07/2025", period_end="10/07/2025"),
            chunk_payload([1], period_start="11/07/2025", period_end="30/06/2026"),
        ])

        self.assertEqual(merged["period_start"], "01/07/2025")
        self.assertEqual(merged["period_end"], "30/06/2026")

    def test_a_zero_balance_is_kept_rather_than_treated_as_missing(self):
        merged = _merge_chunk_results([
            chunk_payload([0], opening_balance=0.0),
            chunk_payload([1], opening_balance=500.0),
        ])

        self.assertEqual(merged["opening_balance"], 0.0)


# ---------------------------------------------------------------------------
# End-to-end extraction over a mocked client
# ---------------------------------------------------------------------------

class ChunkedExtractionTests(TestCase):

    def test_a_24_page_scan_is_extracted_one_call_per_4_page_chunk(self):
        mock = VisionMock()

        with mock.install():
            result = extract_transactions_from_pdf(make_pdf_b64(24), "ANZ.pdf")

        self.assertEqual(len(mock.calls), 6)
        self.assertEqual(len(result["transactions"]), 24)

    def test_transactions_stay_in_page_order_when_chunks_finish_out_of_order(self):
        # Later chunks return fastest, so completion order is the reverse of
        # page order. The merged result must still read front-to-back.
        def slow_early_chunks(pages):
            time.sleep(0.02 * (6 - pages[0] // 4))
            return vision_response(chunk_payload(pages))

        mock = VisionMock(responder=slow_early_chunks)

        with mock.install():
            result = extract_transactions_from_pdf(make_pdf_b64(24), "ANZ.pdf")

        self.assertEqual(
            [t["description"] for t in result["transactions"]],
            [f"page-{i}" for i in range(24)],
        )

    def test_chunks_are_extracted_concurrently(self):
        # Six sequential 0.15s calls would take 0.9s; four at a time, under 0.5s.
        def slow(pages):
            time.sleep(0.15)
            return vision_response(chunk_payload(pages))

        mock = VisionMock(responder=slow)

        with mock.install():
            started = time.monotonic()
            extract_transactions_from_pdf(make_pdf_b64(24), "ANZ.pdf")
            elapsed = time.monotonic() - started

        self.assertLess(elapsed, 0.5)

    def test_the_request_constrains_output_with_a_json_schema(self):
        mock = VisionMock()

        with mock.install():
            extract_transactions_from_pdf(make_pdf_b64(4), "ANZ.pdf")

        output_config = mock.calls[0]["kwargs"]["output_config"]
        self.assertEqual(output_config["format"]["type"], "json_schema")
        schema = output_config["format"]["schema"]
        self.assertIn("transactions", schema["properties"])

    def test_a_truncated_chunk_is_retried_at_half_size(self):
        def truncate_first_chunk(pages):
            if pages == [0, 1, 2, 3]:
                return vision_response(chunk_payload(pages), stop_reason="max_tokens")
            return vision_response(chunk_payload(pages))

        mock = VisionMock(responder=truncate_first_chunk)

        with mock.install():
            result = extract_transactions_from_pdf(make_pdf_b64(8), "ANZ.pdf")

        self.assertIn([0, 1], mock.page_ranges)
        self.assertIn([2, 3], mock.page_ranges)
        self.assertEqual(
            [t["description"] for t in result["transactions"]],
            [f"page-{i}" for i in range(8)],
        )

    def test_a_dense_chunk_keeps_splitting_until_the_pages_fit(self):
        # Production case: pages 5-8 of a scanned ANZ statement truncated, and
        # so did the 2-page half they were split into. Splitting once and
        # giving up failed the whole 24-page statement over two dense pages.
        def truncate_anything_multi_page(pages):
            if len(pages) > 1:
                return vision_response(chunk_payload(pages), stop_reason="max_tokens")
            return vision_response(chunk_payload(pages))

        mock = VisionMock(responder=truncate_anything_multi_page)

        with mock.install():
            result = extract_transactions_from_pdf(make_pdf_b64(8), "ANZ.pdf")

        self.assertEqual(
            [t["description"] for t in result["transactions"]],
            [f"page-{i}" for i in range(8)],
        )
        self.assertIn([5], mock.page_ranges)

    def test_the_output_cap_leaves_room_for_a_dense_scanned_page(self):
        # A single page of a dense scan was measured above 8,000 output
        # tokens, so the old 16,384 cap could not hold even two pages.
        mock = VisionMock()

        with mock.install():
            extract_transactions_from_pdf(make_pdf_b64(4), "ANZ.pdf")

        self.assertGreaterEqual(mock.calls[0]["kwargs"]["max_tokens"], 64000)

    def test_a_chunk_still_truncated_after_the_retry_raises(self):
        mock = VisionMock(
            responder=lambda pages: vision_response(
                chunk_payload(pages), stop_reason="max_tokens"
            )
        )

        with mock.install():
            with self.assertRaises(VisionExtractionError) as ctx:
                extract_transactions_from_pdf(make_pdf_b64(4), "ANZ.pdf")

        self.assertIn("truncated", str(ctx.exception).lower())

    def test_a_single_page_chunk_that_truncates_cannot_be_split_further(self):
        mock = VisionMock(
            responder=lambda pages: vision_response(
                chunk_payload(pages), stop_reason="max_tokens"
            )
        )

        with mock.install():
            with self.assertRaises(VisionExtractionError):
                extract_transactions_from_pdf(make_pdf_b64(1), "ANZ.pdf")

    def test_an_api_failure_raises_with_the_real_cause_instead_of_returning_none(self):
        def boom(pages):
            raise RuntimeError("overloaded_error")

        mock = VisionMock(responder=boom)

        with mock.install():
            with self.assertRaises(VisionExtractionError) as ctx:
                extract_transactions_from_pdf(make_pdf_b64(8), "ANZ.pdf")

        self.assertIn("overloaded_error", str(ctx.exception))

    def test_an_unsplittable_pdf_raises_rather_than_returning_none(self):
        mock = VisionMock()

        with mock.install():
            with self.assertRaises(VisionExtractionError):
                extract_transactions_from_pdf(
                    base64.b64encode(b"not a pdf at all").decode("utf-8"), "junk.pdf"
                )


# ---------------------------------------------------------------------------
# The caller's guard
# ---------------------------------------------------------------------------

class VisionFallbackGuardTests(TestCase):
    """`_try_vision_fallback` is the only production caller of the extractor."""

    PDF = b"%PDF-1.4 image-only scan"

    def test_a_none_result_reports_the_failure_instead_of_crashing(self):
        # The extractor no longer returns None, but a None must not reach
        # `.get()` and resurface as "'NoneType' object has no attribute 'get'"
        # — the message that made a Vision failure look like a bank-detection
        # failure to the user.
        from .views import _try_vision_fallback

        with patch("review.email_ingestion.extract_transactions_from_pdf",
                   return_value=None):
            extracted, message = _try_vision_fallback(self.PDF, "ANZ.pdf")

        self.assertIsNone(extracted)
        self.assertNotIn("NoneType", message)
        self.assertIn("ANZ.pdf", message)

    def test_the_real_vision_error_reaches_the_users_message(self):
        from .views import _try_vision_fallback

        with patch("review.email_ingestion.extract_transactions_from_pdf",
                   side_effect=VisionExtractionError(
                       "Vision output truncated on page 7 of ANZ.pdf")):
            extracted, message = _try_vision_fallback(self.PDF, "ANZ.pdf")

        self.assertIsNone(extracted)
        self.assertIn("truncated on page 7", message)
